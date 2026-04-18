from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from memory_agent_tool.models import ProjectContext, SessionEvent
from memory_agent_tool.services import AppContainer


@pytest.mark.skipif(shutil.which("copilot") is None, reason="copilot CLI is not installed")
def test_copilot_real_adapter_can_handshake_and_run_platform_round_trip(
    container: AppContainer,
    tmp_path: Path,
):
    adapter = container.client_registry.get("copilot_real")
    context = ProjectContext(
        repo_identity=str(tmp_path / "copilot-real-repo"),
        workspace="shared",
        tool_name="copilot",
        working_directory=str(tmp_path),
        client_type="copilot_acp",
        client_session_id="copilot-real-session",
    )

    handshake = adapter.handshake(context)
    assert handshake["agent_name"]
    assert handshake["session_id"]
    assert handshake["models"]

    mounted = adapter.mount_project_memory_server(context)
    assert mounted["session_id"]
    assert Path(mounted["mcp_home"], ".memory-agent-tool", "state.db").exists()

    identified = adapter.identify_project(context)
    assert identified.project_key.endswith("::shared")

    session = adapter.start_session(context)
    emitted = adapter.emit_event(
        session.session_id,
        SessionEvent(
            role_or_event_type="assistant_note",
            content="Copilot ACP integration: real adapter writes shared project memory.",
            memory_type="fact",
            title="copilot acp integration",
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

    recall = adapter.request_recall("shared project memory", context)
    assert "shared project memory" in recall.combined_text.lower()

    mcp_result = adapter.call_project_memory_tool("project_memory_status", {})
    assert mcp_result["service_health"] == "ok"
