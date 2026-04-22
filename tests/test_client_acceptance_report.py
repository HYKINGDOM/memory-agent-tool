from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("copilot") is None or shutil.which("trae") is None, reason="real client CLIs are not installed")
def test_client_acceptance_report_supports_json_and_markdown_and_updates_status(tmp_path: Path):
    env = dict(**__import__("os").environ)
    env["MEMORY_AGENT_TOOL_HOME"] = str(tmp_path)
    project_root = str(Path(__file__).resolve().parents[1])

    json_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "memory_agent_tool.cli",
            "client",
            "report",
            "acceptance",
            "--format",
            "json",
        ],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert json_result.returncode == 0, json_result.stderr
    json_payload = json.loads(json_result.stdout)
    assert json_payload["format"] == "json"
    assert "copilot" in json_payload["clients"]
    assert "trae" in json_payload["clients"]

    md_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "memory_agent_tool.cli",
            "client",
            "report",
            "acceptance",
            "--format",
            "markdown",
        ],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert md_result.returncode == 0, md_result.stderr
    assert md_result.stdout.startswith("# Real Client Acceptance Report")
