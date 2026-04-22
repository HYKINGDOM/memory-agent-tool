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
    project_root = str(Path(__file__).resolve().parents[1])
    result = subprocess.run(
        [sys.executable, "-m", "memory_agent_tool.cli", "client", "copilot", "e2e"],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("trae") is None, reason="trae CLI is not installed")
def test_cli_trae_mount_command_runs_real_integration(tmp_path: Path):
    env = dict(**__import__("os").environ)
    env["MEMORY_AGENT_TOOL_HOME"] = str(tmp_path)
    project_root = str(Path(__file__).resolve().parents[1])
    result = subprocess.run(
        [sys.executable, "-m", "memory_agent_tool.cli", "client", "trae", "mount"],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("trae") is None, reason="trae CLI is not installed")
def test_cli_trae_chat_e2e_command_runs_end_to_end(tmp_path: Path):
    env = dict(**__import__("os").environ)
    env["MEMORY_AGENT_TOOL_HOME"] = str(tmp_path)
    project_root = str(Path(__file__).resolve().parents[1])
    result = subprocess.run(
        [sys.executable, "-m", "memory_agent_tool.cli", "client", "trae", "chat-e2e"],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
