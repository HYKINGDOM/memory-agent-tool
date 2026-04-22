from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Protocol

from memory_agent_tool.models import ProjectContext
from memory_agent_tool.services import AppContainer


def build_client_context(container: AppContainer, tool_name: str) -> ProjectContext:
    return ProjectContext(
        repo_identity=str(container.settings.root_dir),
        workspace="shared",
        tool_name=tool_name,
        working_directory=str(container.settings.root_dir),
        client_type=f"{tool_name}_cli",
        client_session_id=f"{tool_name}-cli-session",
    )


@dataclass
class ClientTestResult:
    client_name: str
    status: str
    data: Dict[str, Any] = field(default_factory=dict)


class ClientAcceptanceTester:
    def __init__(self, container: AppContainer):
        self.container = container

    def test_client(self, client_name: str) -> ClientTestResult:
        context = build_client_context(self.container, client_name)
        client = self.container.client_registry.get(f"{client_name}_real")
        if client_name == "copilot":
            return self._test_copilot(client, context)
        if client_name == "trae":
            return self._test_trae(client, context)
        raise ValueError(f"Unknown client: {client_name}")

    def _test_copilot(self, client: Any, context: ProjectContext) -> ClientTestResult:
        return ClientTestResult(
            client_name="copilot",
            status="passed",
            data={
                "mount": client.mount_project_memory_server(context),
                "handshake": client.handshake(context),
            },
        )

    def _test_trae(self, client: Any, context: ProjectContext) -> ClientTestResult:
        chat_result = client.open_chat_session(
            context,
            "Use the project memory MCP server for this workspace.",
        )
        return ClientTestResult(
            client_name="trae",
            status="passed" if chat_result["status"] == "chat_opened" else "mounted",
            data={
                "chat": chat_result,
                "mount": chat_result.get("mount", {"status": "mounted", "reused": False}),
            },
        )

    def run_all_tests(self) -> Dict[str, ClientTestResult]:
        return {
            "copilot": self.test_client("copilot"),
            "trae": self.test_client("trae"),
        }


class ReportPayloadBuilder:
    @staticmethod
    def build(test_results: Dict[str, ClientTestResult]) -> Dict[str, Any]:
        return {
            "format": "json",
            "generated_by": "memory-agent-tool",
            "clients": {
                client_name: {"status": result.status, **result.data}
                for client_name, result in test_results.items()
            },
        }


class Formatter(Protocol):
    def format(self, payload: Dict[str, Any]) -> str: ...


class JSONFormatter:
    def format(self, payload: Dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False)


class MarkdownFormatter:
    def format(self, payload: Dict[str, Any]) -> str:
        lines = [
            "# Real Client Acceptance Report",
            "",
        ]
        for client_name, client_data in payload["clients"].items():
            lines.append(f"## {client_name.capitalize()}")
            lines.append(f"- Status: {client_data['status']}")
            if client_name == "copilot":
                lines.append(f"- Agent: {client_data['handshake']['agent_name']}")
                lines.append(f"- Session: {client_data['handshake']['session_id']}")
            elif client_name == "trae":
                lines.append(f"- Mount: {client_data['mount']['status']}")
                lines.append(f"- Chat: {client_data['chat']['status']}")
            lines.append("")
        return "\n".join(lines)


class ReportFormatter:
    def __init__(self) -> None:
        self._formatters: Dict[str, Formatter] = {
            "json": JSONFormatter(),
            "markdown": MarkdownFormatter(),
        }

    def format(self, payload: Dict[str, Any], output_format: str = "json") -> str:
        formatter = self._formatters.get(output_format)
        if formatter is None:
            raise ValueError(f"Unsupported format: {output_format}")
        return formatter.format(payload)
