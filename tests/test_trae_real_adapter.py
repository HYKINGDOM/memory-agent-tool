from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from memory_agent_tool.models import FeedbackRequest, ProjectContext, SessionEvent
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
    assert mount["status"] == "mounted"

    session = adapter.start_session(context)
    emitted = adapter.emit_event(
        session.session_id,
        SessionEvent(
            role_or_event_type="assistant_note",
            content="Trae real integration: project memory bridge is mounted.",
            memory_type="fact",
            title="trae integration",
        ),
        context,
    )
    assert emitted is not None

    recall = adapter.request_recall("project memory bridge", context)
    assert recall.combined_text


def test_trae_real_adapter_open_chat_mounts_inline_once(
    container: AppContainer,
    tmp_path: Path,
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

    chat = adapter.open_chat_session(context, "Use project memory.")
    assert chat["status"] == "chat_opened"
    assert chat["mount"]["status"] == "mounted"
