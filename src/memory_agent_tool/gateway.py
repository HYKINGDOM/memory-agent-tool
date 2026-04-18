from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Any

from memory_agent_tool.copilot_acp import CopilotACPClient
from memory_agent_tool.models import FeedbackRequest, MemoryRecallRequest, ProjectContext, SessionEvent, SessionStartRequest


class ClientAdapter(ABC):
    @abstractmethod
    def identify_project(self, context: ProjectContext):
        raise NotImplementedError

    @abstractmethod
    def start_session(self, context: ProjectContext):
        raise NotImplementedError

    @abstractmethod
    def emit_event(self, session_id: str, event: SessionEvent):
        raise NotImplementedError

    @abstractmethod
    def end_session(self, session_id: str, context: ProjectContext):
        raise NotImplementedError

    @abstractmethod
    def request_recall(self, query: str, context: ProjectContext):
        raise NotImplementedError

    @abstractmethod
    def submit_feedback(self, request: FeedbackRequest):
        raise NotImplementedError

    def handshake(self, context: ProjectContext):
        raise NotImplementedError

    def call_project_memory_tool(self, name: str, arguments: dict[str, Any]):
        raise NotImplementedError

    def prompt_with_project_memory(self, prompt: str, context: ProjectContext):
        raise NotImplementedError

    def mount_project_memory_server(self, context: ProjectContext):
        raise NotImplementedError


class _BaseAdapter(ClientAdapter):
    def __init__(self, container, tool_name: str):
        self.container = container
        self.tool_name = tool_name

    def identify_project(self, context: ProjectContext):
        return self.container.projects.ensure_project(context)

    def start_session(self, context: ProjectContext):
        return self.container.archive.start_session(SessionStartRequest(project=context, source_channel=self.tool_name))

    def emit_event(self, session_id: str, event: SessionEvent):
        working_context = ProjectContext(
            repo_identity=event.metadata.get("repo_identity") if getattr(event, "metadata", None) else "",
            workspace=event.metadata.get("workspace") if getattr(event, "metadata", None) else None,
            namespace=event.metadata.get("namespace") if getattr(event, "metadata", None) else None,
            branch=event.metadata.get("branch") if getattr(event, "metadata", None) else None,
            tool_name=self.tool_name,
            working_directory=event.metadata.get("working_directory") if getattr(event, "metadata", None) else None,
        )
        return self.container.archive.append_event(session_id, event, working_context)

    def end_session(self, session_id: str, context: ProjectContext):
        return self.container.archive.end_session(session_id, context)

    def request_recall(self, query: str, context: ProjectContext):
        return self.container.retrieval.recall(MemoryRecallRequest(project=context, query=query))

    def submit_feedback(self, request: FeedbackRequest):
        return self.container.conflicts.apply_feedback(request)

    def handshake(self, context: ProjectContext):
        raise NotImplementedError(f"{self.__class__.__name__} does not support handshake")

    def call_project_memory_tool(self, name: str, arguments: dict[str, Any]):
        raise NotImplementedError(f"{self.__class__.__name__} does not support MCP tool calls")

    def prompt_with_project_memory(self, prompt: str, context: ProjectContext):
        raise NotImplementedError(f"{self.__class__.__name__} does not support MCP prompting")

    def mount_project_memory_server(self, context: ProjectContext):
        raise NotImplementedError(f"{self.__class__.__name__} does not support MCP mounting")


class TraeAdapter(_BaseAdapter):
    pass


class TraeRealAdapter(_BaseAdapter):
    def __init__(self, container, tool_name: str):
        super().__init__(container, tool_name)
        self._trae_command = "trae"
        self._mounted_user_data_dirs: dict[str, Path] = {}

    def _build_mcp_definition(self, home: Path) -> str:
        executable = Path(__file__).resolve().parents[2] / ".venv" / "bin" / "memory-agent-tool"
        command = str(executable) if executable.exists() else "memory-agent-tool"
        payload = {
            "name": "project-memory",
            "command": command,
            "args": ["mcp", "serve"],
            "env": {
                "MEMORY_AGENT_TOOL_HOME": str(home),
            },
        }
        return json.dumps(payload, ensure_ascii=False)

    def _context_key(self, context: ProjectContext) -> str:
        return "|".join(
            [
                context.working_directory or "",
                context.repo_identity,
                context.workspace or "",
                context.client_type or "",
                context.client_session_id or "",
            ]
        )

    def _build_mount_payload(
        self,
        home: Path,
        user_data_dir: Path,
        *,
        reused: bool,
        output: str = "",
    ) -> dict[str, Any]:
        return {
            "client": "trae",
            "status": "mounted",
            "mcp_home": str(home),
            "user_data_dir": str(user_data_dir),
            "reused": reused,
            "output": output,
        }

    def _wait_for_state_db(self, home: Path) -> None:
        state_db = home / ".memory-agent-tool" / "state.db"
        deadline = time.time() + 10
        while time.time() < deadline and not state_db.exists():
            time.sleep(0.1)

    def mount_project_memory_server(self, context: ProjectContext):
        home = self.container.settings.root_dir
        context_key = self._context_key(context)
        existing_user_data_dir = self._mounted_user_data_dirs.get(context_key)
        if existing_user_data_dir is not None:
            return self._build_mount_payload(home, existing_user_data_dir, reused=True)

        user_data_dir = Path(tempfile.mkdtemp(prefix="memory-agent-tool-trae-"))
        result = subprocess.run(
            [
                self._trae_command,
                "--add-mcp",
                self._build_mcp_definition(home),
                "--user-data-dir",
                str(user_data_dir),
                "--new-window",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=context.working_directory or str(self.container.settings.root_dir),
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Failed to mount MCP server in Trae")
        self._mounted_user_data_dirs[context_key] = user_data_dir
        self._wait_for_state_db(home)
        return self._build_mount_payload(
            home,
            user_data_dir,
            reused=False,
            output=(result.stdout or "").strip(),
        )

    def open_chat_session(self, context: ProjectContext, prompt: str) -> dict[str, Any]:
        home = self.container.settings.root_dir
        context_key = self._context_key(context)
        user_data_dir = self._mounted_user_data_dirs.get(context_key)
        mount_args: list[str] = []
        mount_payload: dict[str, Any]

        if user_data_dir is None:
            user_data_dir = Path(tempfile.mkdtemp(prefix="memory-agent-tool-trae-chat-"))
            mount_args = ["--add-mcp", self._build_mcp_definition(home)]
            mount_payload = self._build_mount_payload(home, user_data_dir, reused=False)
        else:
            mount_payload = self._build_mount_payload(home, user_data_dir, reused=True)

        result = subprocess.run(
            [
                self._trae_command,
                *mount_args,
                "--user-data-dir",
                str(user_data_dir),
                "--new-window",
                "chat",
                prompt,
                "--mode",
                "agent",
                "--new-window",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=context.working_directory or str(self.container.settings.root_dir),
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Failed to open Trae chat session")
        self._mounted_user_data_dirs[context_key] = user_data_dir
        self._wait_for_state_db(home)
        return {
            "client": "trae",
            "status": "chat_opened",
            "mcp_home": str(home),
            "output": (result.stdout or "").strip(),
            "mount": mount_payload,
        }


class CopilotAdapter(_BaseAdapter):
    pass


@dataclass(slots=True)
class _CopilotHandshake:
    client: CopilotACPClient
    session_id: str
    init_result: dict[str, Any]
    session_result: dict[str, Any]


class CopilotRealAdapter(_BaseAdapter):
    def __init__(self, container, tool_name: str):
        super().__init__(container, tool_name)
        self._request_id = 0

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _build_mcp_server_config(self) -> dict[str, Any]:
        executable = Path(__file__).resolve().parents[2] / ".venv" / "bin" / "memory-agent-tool"
        command = str(executable) if executable.exists() else "memory-agent-tool"
        mcp_home = self.container.settings.root_dir
        return {
            "name": "project-memory",
            "command": command,
            "args": ["mcp", "serve"],
            "env": [
                {"name": "MEMORY_AGENT_TOOL_HOME", "value": str(mcp_home)},
            ],
        }

    def mount_project_memory_server(self, context: ProjectContext):
        handshake = self._open_session(context, with_mcp=True)
        try:
            home = self.container.settings.root_dir
            state_db = home / ".memory-agent-tool" / "state.db"
            deadline = __import__("time").time() + 10
            while __import__("time").time() < deadline and not state_db.exists():
                __import__("time").sleep(0.1)
            return {
                "session_id": handshake.session_id,
                "mcp_home": str(home),
            }
        finally:
            handshake.client.close()

    def _open_session(self, context: ProjectContext, with_mcp: bool) -> _CopilotHandshake:
        client = CopilotACPClient(cwd=context.working_directory or str(self.container.settings.root_dir))
        client.start()
        init_message = client.request(
            self._next_id(),
            "initialize",
            {
                "protocolVersion": 1,
                "clientCapabilities": {
                    "fs": {
                        "readTextFile": True,
                        "writeTextFile": True,
                    }
                },
                "clientInfo": {
                    "name": "memory-agent-tool",
                    "title": "memory-agent-tool",
                    "version": "0.1.0",
                },
            },
        )
        session_message = client.request(
            self._next_id(),
            "session/new",
            {
                "cwd": context.working_directory or str(self.container.settings.root_dir),
                "mcpServers": [self._build_mcp_server_config()] if with_mcp else [],
            },
            timeout_seconds=90.0,
        )
        session_id = str(session_message["result"]["sessionId"])
        return _CopilotHandshake(
            client=client,
            session_id=session_id,
            init_result=init_message["result"],
            session_result=session_message["result"],
        )

    def handshake(self, context: ProjectContext):
        handshake = self._open_session(context, with_mcp=False)
        try:
            result = {
                "agent_name": handshake.init_result["agentInfo"]["name"],
                "session_id": handshake.session_id,
                "models": handshake.session_result.get("models", {}),
            }
            return result
        finally:
            handshake.client.close()

    def call_project_memory_tool(self, name: str, arguments: dict[str, Any]):
        return self.container.codex_mcp.call_tool(name, arguments)

    def prompt_with_project_memory(self, prompt: str, context: ProjectContext):
        handshake = self._open_session(context, with_mcp=True)

        def _mcp_handler(message: dict[str, Any], process) -> bool:
            params = message.get("params") or {}
            if message.get("method") != "mcp/callTool":
                return False
            result = self.container.codex_mcp.call_tool(
                params["name"],
                params.get("arguments") or {},
            )
            response = {
                "jsonrpc": "2.0",
                "id": message.get("id"),
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": str(result),
                        }
                    ],
                    "structuredContent": result,
                },
            }
            assert process.stdin is not None
            process.stdin.write(json.dumps(response) + "\n")
            process.stdin.flush()
            return True

        try:
            text_parts: list[str] = []
            handshake.client.request(
                self._next_id(),
                "session/prompt",
                {
                    "sessionId": handshake.session_id,
                    "prompt": [
                        {
                            "type": "text",
                            "text": prompt,
                        }
                    ],
                },
                timeout_seconds=90.0,
                text_parts=text_parts,
                extra_handlers={"mcp/callTool": _mcp_handler},
            )
            return "".join(text_parts).strip()
        finally:
            handshake.client.close()


class ClientRegistry:
    def __init__(self, container):
        self._adapters = {
            "trae": TraeAdapter(container, "trae"),
            "trae_real": TraeRealAdapter(container, "trae"),
            "copilot": CopilotAdapter(container, "copilot"),
            "copilot_real": CopilotRealAdapter(container, "copilot"),
        }

    def get(self, name: str) -> ClientAdapter:
        return self._adapters[name]
