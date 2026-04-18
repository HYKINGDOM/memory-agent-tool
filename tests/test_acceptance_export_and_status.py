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
        cwd="/Users/zc/Documents/memory-agent-tool",
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert json_result.returncode == 0, json_result.stderr
    json_payload = json.loads(json_result.stdout)
    assert json_payload["format"] == "json"
    assert json_payload["clients"]["copilot"]["status"] == "passed"
    assert json_payload["clients"]["trae"]["status"] in {"mounted", "passed"}

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
        cwd="/Users/zc/Documents/memory-agent-tool",
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert md_result.returncode == 0, md_result.stderr
    assert md_result.stdout.startswith("# Real Client Acceptance Report")
    assert "Copilot" in md_result.stdout
    assert "Trae" in md_result.stdout

    status_result = subprocess.run(
        [sys.executable, "-m", "memory_agent_tool.cli", "report", "status"],
        cwd="/Users/zc/Documents/memory-agent-tool",
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert status_result.returncode == 0, status_result.stderr
    status_payload = json.loads(status_result.stdout)
    assert status_payload["recent_client_acceptance_result"] is not None
    assert status_payload["recent_client_acceptance_result"]["clients"]["copilot"]["status"] == "passed"


def test_agents_skill_and_project_docs_exist_and_cover_correct_paths():
    agents_path = Path("/Users/zc/Documents/memory-agent-tool/AGENTS.md")
    skill_path = Path("/Users/zc/Documents/memory-agent-tool/.memory-agent-tool/skills/project_delivery/SKILL.md")
    project_doc = Path("/Users/zc/Documents/memory-agent-tool/docs/project/project-delivery.md")
    usage_doc = Path("/Users/zc/Documents/memory-agent-tool/docs/usage/client-integration.md")
    readme_path = Path("/Users/zc/Documents/memory-agent-tool/README.md")

    for path in (agents_path, skill_path, project_doc, usage_doc, readme_path):
        assert path.exists(), str(path)

    agents = agents_path.read_text(encoding="utf-8")
    skill = skill_path.read_text(encoding="utf-8")
    project = project_doc.read_text(encoding="utf-8")
    usage = usage_doc.read_text(encoding="utf-8")
    readme = readme_path.read_text(encoding="utf-8")

    assert "memory-agent-tool client report acceptance --format json" in agents
    assert "memory-agent-tool client report acceptance --format markdown" in agents
    assert "Copilot ACP" in project
    assert "Trae CLI" in project
    assert "client copilot e2e" in usage
    assert "client trae chat-e2e" in usage
    assert "project_delivery" in skill
    assert "client report acceptance" in readme
