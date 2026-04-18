from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass
from typing import Any

from memory_agent_tool.config import AppSettings
from memory_agent_tool.services import AppContainer


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class MCPServerRuntime:
    container: AppContainer

    @classmethod
    def build(cls, settings: AppSettings | None = None) -> "MCPServerRuntime":
        resolved = settings or AppSettings.from_env()
        return cls(container=AppContainer.build(resolved))

    def run_stdio(self) -> int:
        for line in sys.stdin:
            if not line.strip():
                continue
            try:
                message = json.loads(line)
                response = self._handle_message(message)
            except Exception as exc:
                response = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32603,
                        "message": str(exc),
                    },
                }
            if response is not None:
                sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                sys.stdout.flush()
        return 0

    def _handle_message(self, message: dict[str, Any]) -> dict[str, Any] | None:
        message_id = message.get("id")
        method = message.get("method")
        params = message.get("params") or {}
        if method == "notifications/initialized":
            return None
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": message_id,
                "result": {
                    "protocolVersion": params.get("protocolVersion", "2025-03-26"),
                    "serverInfo": {
                        "name": "memory-agent-tool",
                        "version": "0.1.0",
                    },
                    "capabilities": {
                        "tools": {"listChanged": False},
                    },
                },
            }
        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": message_id,
                "result": {
                    "tools": [
                        {
                            "name": tool["name"],
                            "description": tool["description"],
                            "inputSchema": {"type": "object"},
                        }
                        for tool in self.container.codex_mcp.list_tools()
                    ]
                },
            }
        if method == "tools/call":
            name = params["name"]
            arguments = params.get("arguments") or {}
            result = self.container.codex_mcp.call_tool(name, arguments)
            return {
                "jsonrpc": "2.0",
                "id": message_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(result, ensure_ascii=False, indent=2),
                        }
                    ],
                    "structuredContent": result,
                },
            }
        return {
            "jsonrpc": "2.0",
            "id": message_id,
            "error": {
                "code": -32601,
                "message": f"Unsupported MCP method: {method}",
            },
        }
