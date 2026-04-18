from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _run_cli(tmp_path: Path, *args: str):
    env = dict(**__import__("os").environ)
    env["MEMORY_AGENT_TOOL_HOME"] = str(tmp_path)
    return subprocess.run(
        [sys.executable, "-m", "memory_agent_tool.cli", *args],
        cwd="/Users/zc/Documents/memory-agent-tool",
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_cli_provider_observability_and_runtime_config_commands(tmp_path: Path):
    provider_report = _run_cli(tmp_path, "report", "providers")
    assert provider_report.returncode == 0, provider_report.stderr
    report_payload = json.loads(provider_report.stdout)
    assert "statuses" in report_payload

    provider_config = _run_cli(tmp_path, "providers", "config")
    assert provider_config.returncode == 0, provider_config.stderr
    config_payload = json.loads(provider_config.stdout)
    assert "enabled_providers" in config_payload

    update_config = _run_cli(
        tmp_path,
        "providers",
        "config",
        "enabled_providers",
        '["local_builtin","holographic_like"]',
    )
    assert update_config.returncode == 0, update_config.stderr
    updated_payload = json.loads(update_config.stdout)
    assert updated_payload["enabled_providers"] == ["local_builtin", "holographic_like"]


def test_cli_project_alias_and_scope_report_commands(tmp_path: Path):
    status_result = _run_cli(tmp_path, "report", "status")
    assert status_result.returncode == 0, status_result.stderr
    status_payload = json.loads(status_result.stdout)
    assert status_payload["stats"]["projects"] == 0

    register = _run_cli(tmp_path, "projects", "alias", "legacy::shared", "repo::shared")
    assert register.returncode == 0, register.stderr
    register_payload = json.loads(register.stdout)
    assert register_payload["alias_key"] == "legacy::shared"


def test_cli_maintenance_commands_run_without_errors(tmp_path: Path):
    for command in (
        ("maintenance", "rebuild-summaries"),
        ("maintenance", "review-stale", "repo::shared"),
        ("maintenance", "consolidate", "repo::shared"),
    ):
        result = _run_cli(tmp_path, *command)
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert isinstance(payload, dict)
