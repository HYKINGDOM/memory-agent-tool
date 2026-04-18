from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def test_real_client_acceptance_doc_exists_and_covers_boundaries():
    doc_path = Path("/Users/zc/Documents/memory-agent-tool/docs/integrations/real-clients.md")
    assert doc_path.exists()
    content = doc_path.read_text(encoding="utf-8")
    assert "Copilot" in content
    assert "Trae" in content
    assert "真实边界" in content
    assert "已验证链路" in content
    assert "依赖" in content


@pytest.mark.skipif(shutil.which("copilot") is None or shutil.which("trae") is None, reason="real client CLIs are not installed")
def test_cli_client_acceptance_report_command_outputs_both_clients(tmp_path: Path):
    env = dict(**__import__("os").environ)
    env["MEMORY_AGENT_TOOL_HOME"] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, "-m", "memory_agent_tool.cli", "client", "report", "acceptance"],
        cwd="/Users/zc/Documents/memory-agent-tool",
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "copilot" in payload["clients"]
    assert "trae" in payload["clients"]
    assert payload["clients"]["copilot"]["status"] == "passed"
    assert payload["clients"]["trae"]["status"] in {"mounted", "passed"}
