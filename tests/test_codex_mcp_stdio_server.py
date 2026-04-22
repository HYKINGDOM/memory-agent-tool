from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

try:
    from memory_agent_tool.mcp_server import MCPServerRuntime
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False


def _request(proc: subprocess.Popen[str], request_id: int, method: str, params: dict, timeout: float = 20.0):
    assert proc.stdin is not None
    assert proc.stdout is not None
    proc.stdin.write(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
        )
        + "\n"
    )
    proc.stdin.flush()
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        message = json.loads(line)
        if message.get("id") == request_id:
            return message
    raise TimeoutError(f"Timed out waiting for MCP response to {method}")


@pytest.mark.skipif(not MCP_AVAILABLE, reason="MCP server module not available")
def test_codex_mcp_stdio_server_exposes_tools_and_status(tmp_path: Path):
    env = os.environ.copy()
    env["MEMORY_AGENT_TOOL_HOME"] = str(tmp_path)
    proc = subprocess.Popen(
        [sys.executable, "-m", "memory_agent_tool.cli", "mcp", "serve"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=env,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    try:
        initialize = _request(
            proc,
            1,
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "clientInfo": {"name": "pytest", "version": "0.0.0"},
                "capabilities": {},
            },
        )
        assert initialize["result"]["serverInfo"]["name"] == "memory-agent-tool"

        tools = _request(proc, 2, "tools/list", {})
        tool_names = {tool["name"] for tool in tools["result"]["tools"]}
        assert "start_session" in tool_names
        assert "recall_memory" in tool_names

        status = _request(
            proc,
            3,
            "tools/call",
            {"name": "health_check", "arguments": {}},
        )
        payload = json.loads(status["result"]["content"][0]["text"])
        assert payload["status"] == "ok"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
