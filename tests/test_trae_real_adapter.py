from __future__ import annotations

import shutil
from types import SimpleNamespace
from pathlib import Path

import pytest

from memory_agent_tool.models import ProjectContext, SessionEvent
from memory_agent_tool.services import AppContainer


@pytest.mark.skipif(shutil.which("trae") is None, reason="trae CLI is not installed")
def test_trae_real_adapter_can_mount_and_round_trip(
    container: AppContainer,
    tmp_path: Path,
):
    adapter = container.client_registry.get("trae_real")
    context = ProjectContext(
        repo_identity=str(tmp_path / "trae-real-repo"),
        workspace="shared",
        tool_name="trae",
        working_directory=str(tmp_path),
        client_type="trae_cli",
        client_session_id="trae-real-session",
    )

    mount = adapter.mount_project_memory_server(context)
    assert mount["client"] == "trae"
    assert mount["status"] == "mounted"
    assert Path(mount["mcp_home"], ".memory-agent-tool", "state.db").exists()

    identified = adapter.identify_project(context)
    session = adapter.start_session(context)
    emitted = adapter.emit_event(
        session.session_id,
        SessionEvent(
            role_or_event_type="assistant_note",
            content="Trae real integration: project memory bridge is mounted.",
            memory_type="fact",
            title="trae integration",
            metadata={
                "repo_identity": context.repo_identity,
                "workspace": context.workspace,
                "working_directory": context.working_directory,
                "client_type": context.client_type,
                "client_session_id": context.client_session_id,
            },
        ),
    )
    assert emitted["project_key"] == identified.project_key

    recall = adapter.request_recall("project memory bridge", context)
    assert "project memory bridge" in recall.combined_text.lower()

    feedback = adapter.submit_feedback(
        __import__("memory_agent_tool.models", fromlist=["FeedbackRequest"]).FeedbackRequest(
            memory_id=emitted["ingested_memory"]["memory_id"],
            helpful=True,
        )
    )
    assert feedback.trust_score >= 0.6


def test_trae_real_adapter_open_chat_mounts_inline_once(
    container: AppContainer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    adapter = container.client_registry.get("trae_real")
    context = ProjectContext(
        repo_identity=str(tmp_path / "trae-inline-repo"),
        workspace="shared",
        tool_name="trae",
        working_directory=str(tmp_path),
        client_type="trae_cli",
        client_session_id="trae-inline-session",
    )

    launched_args: list[list[str]] = []
    user_data_dir = tmp_path / "trae-inline-profile"
    user_data_dir.mkdir()

    monkeypatch.setattr(
        "memory_agent_tool.gateway.tempfile.mkdtemp",
        lambda prefix: str(user_data_dir),
    )

    def fake_run(args, **kwargs):
        launched_args.append(list(args))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("memory_agent_tool.gateway.subprocess.run", fake_run)

    chat = adapter.open_chat_session(context, "Use project memory.")

    assert chat["status"] == "chat_opened"
    assert chat["mount"]["status"] == "mounted"
    assert chat["mount"]["reused"] is False
    assert len(launched_args) == 1
    assert "--add-mcp" in launched_args[0]


def test_trae_real_adapter_open_chat_reuses_existing_mount(
    container: AppContainer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    adapter = container.client_registry.get("trae_real")
    context = ProjectContext(
        repo_identity=str(tmp_path / "trae-reuse-repo"),
        workspace="shared",
        tool_name="trae",
        working_directory=str(tmp_path),
        client_type="trae_cli",
        client_session_id="trae-reuse-session",
    )

    launched_args: list[list[str]] = []
    user_data_dir = tmp_path / "trae-reuse-profile"
    user_data_dir.mkdir()

    monkeypatch.setattr(
        "memory_agent_tool.gateway.tempfile.mkdtemp",
        lambda prefix: str(user_data_dir),
    )

    def fake_run(args, **kwargs):
        launched_args.append(list(args))
        return SimpleNamespace(returncode=0, stdout="Added MCP servers: project-memory", stderr="")

    monkeypatch.setattr("memory_agent_tool.gateway.subprocess.run", fake_run)

    mount = adapter.mount_project_memory_server(context)
    chat = adapter.open_chat_session(context, "Use project memory.")

    assert mount["status"] == "mounted"
    assert len(launched_args) == 2
    assert "--add-mcp" in launched_args[0]
    assert "--add-mcp" not in launched_args[1]
    assert str(user_data_dir) in launched_args[0]
    assert str(user_data_dir) in launched_args[1]
    assert chat["mount"]["status"] == "mounted"
    assert chat["mount"]["reused"] is True
