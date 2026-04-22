from __future__ import annotations

import json
import sys
from typing import Any

from memory_agent_tool.config import AppSettings
from memory_agent_tool.logging import get_logger, setup_logging
from memory_agent_tool.mcp import CodexMCPServer
from memory_agent_tool.services import AppContainer

logger = get_logger("mcp_server")


class MCPServerRuntime:
    def __init__(self, container: AppContainer):
        self.container = container
        self.codex_mcp = container.codex_mcp

    @classmethod
    def build(cls, settings: AppSettings | None = None) -> "MCPServerRuntime":
        resolved = settings or AppSettings.from_env()
        container = AppContainer.build(resolved)
        return cls(container)

    def run_stdio(self) -> int:
        try:
            return self._stdio_loop()
        except KeyboardInterrupt:
            return 0

    def _stdio_loop(self) -> int:
        while True:
            line = sys.stdin.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                sys.stderr.write(json.dumps({"error": "invalid JSON"}) + "\n")
                sys.stderr.flush()
                continue
            response = self._dispatch(message)
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        return 0

    def _dispatch(self, message: dict[str, Any]) -> dict[str, Any]:
        method = message.get("method", "")
        params = message.get("params", {})
        request_id = message.get("id")
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "memory-agent-tool", "version": "0.1.0"},
                },
            }
        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "tools": [
                        {
                            "name": "start_session",
                            "description": "Start a new project memory session.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "project": {"type": "object", "description": "Project context."},
                                    "source_channel": {"type": "string", "default": "mcp"},
                                },
                                "required": ["project"],
                            },
                        },
                        {
                            "name": "append_event",
                            "description": "Append an event to a session.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "session_id": {"type": "string"},
                                    "event": {"type": "object"},
                                    "project": {"type": "object"},
                                },
                                "required": ["session_id", "event", "project"],
                            },
                        },
                        {
                            "name": "end_session",
                            "description": "End a project memory session.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "session_id": {"type": "string"},
                                    "project": {"type": "object"},
                                },
                                "required": ["session_id", "project"],
                            },
                        },
                        {
                            "name": "ingest_memory",
                            "description": "Ingest a memory item directly.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "project": {"type": "object"},
                                    "content": {"type": "string"},
                                    "memory_type": {"type": "string", "default": "fact"},
                                    "title": {"type": "string"},
                                },
                                "required": ["project", "content"],
                            },
                        },
                        {
                            "name": "recall_memory",
                            "description": "Recall relevant memory for a query.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "project": {"type": "object"},
                                    "query": {"type": "string"},
                                    "limit": {"type": "integer", "default": 3},
                                },
                                "required": ["project", "query"],
                            },
                        },
                        {
                            "name": "apply_feedback",
                            "description": "Apply feedback to a memory item.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "memory_id": {"type": "integer"},
                                    "helpful": {"type": "boolean"},
                                },
                                "required": ["memory_id", "helpful"],
                            },
                        },
                        {
                            "name": "status_report",
                            "description": "Get the current status report.",
                            "inputSchema": {"type": "object", "properties": {}},
                        },
                        {
                            "name": "health_check",
                            "description": "Health check.",
                            "inputSchema": {"type": "object", "properties": {}},
                        },
                    ]
                },
            }
        if method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            result = self.codex_mcp.handle_tool_call(tool_name, arguments)
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                },
            }
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }
