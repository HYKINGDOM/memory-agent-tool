from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("copilot") is None, reason="copilot CLI is not installed")
def test_cli_copilot_e2e_command_runs_end_to_end(tmp_path: Path):
    env = dict(**__import__("os").environ)
    env["MEMORY_AGENT_TOOL_HOME"] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, "-m", "memory_agent_tool.cli", "client", "copilot", "e2e"],
        cwd="/Users/zc/Documents/memory-agent-tool",
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["client"] == "copilot"
    assert payload["status"] == "passed"
    assert payload["feedback"]["trust_score"] >= 0.6


@pytest.mark.skipif(shutil.which("trae") is None, reason="trae CLI is not installed")
def test_cli_trae_mount_command_runs_real_integration(tmp_path: Path):
    env = dict(**__import__("os").environ)
    env["MEMORY_AGENT_TOOL_HOME"] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, "-m", "memory_agent_tool.cli", "client", "trae", "mount"],
        cwd="/Users/zc/Documents/memory-agent-tool",
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["client"] == "trae"
    assert payload["status"] == "mounted"
    assert Path(payload["mcp_home"], ".memory-agent-tool", "state.db").exists()


@pytest.mark.skipif(shutil.which("trae") is None, reason="trae CLI is not installed")
def test_cli_trae_chat_e2e_command_runs_end_to_end(tmp_path: Path):
    env = dict(**__import__("os").environ)
    env["MEMORY_AGENT_TOOL_HOME"] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, "-m", "memory_agent_tool.cli", "client", "trae", "chat-e2e"],
        cwd="/Users/zc/Documents/memory-agent-tool",
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["client"] == "trae"
    assert payload["status"] == "passed"
    assert payload["recall_contains"] is True
    assert payload["feedback"]["trust_score"] >= 0.6
