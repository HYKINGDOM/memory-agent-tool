from __future__ import annotations

from pathlib import Path

from memory_agent_tool.models import FeedbackRequest, MemoryRecallRequest, ProjectContext, SessionEvent, SessionStartRequest
from memory_agent_tool.services import AppContainer


def make_context(tmp_path: Path) -> ProjectContext:
    return ProjectContext(
        repo_identity=str(tmp_path / "repo"),
        workspace="shared",
        tool_name="codex",
        working_directory=str(tmp_path),
        client_type="codex_mcp",
        client_session_id="codex-client-1",
    )


def test_codex_mcp_round_trip(container: AppContainer, tmp_path: Path):
    context = make_context(tmp_path)
    resolved = container.projects.ensure_project(context)
    assert resolved.project_key.endswith("::shared")

    session = container.archive.start_session(SessionStartRequest(project=context))
    event_result = container.archive.append_event(
        session.session_id,
        SessionEvent(
            role_or_event_type="assistant_note",
            content="API framework: FastAPI",
            memory_type="fact",
            title="api framework",
        ),
        context,
    )
    assert event_result.ingested_memory is not None
    assert event_result.ingested_memory.state.value == "pinned_active"

    recall = container.retrieval.recall(
        MemoryRecallRequest(project=context, query="api framework")
    )
    assert "FastAPI" in recall.combined_text

    feedback = container.conflicts.apply_feedback(
        FeedbackRequest(memory_id=event_result.ingested_memory.memory_id, helpful=True)
    )
    assert feedback.trust_score > 0.6

    health = container.reporter.health()
    assert health.status == "ok"


def test_trae_and_copilot_adapters_follow_contract(container: AppContainer, tmp_path: Path):
    for adapter_name in ("trae_real", "copilot_real"):
        adapter = container.client_registry.get(adapter_name)
        tool_name = "trae" if adapter_name == "trae_real" else "copilot"
        context = ProjectContext(
            repo_identity=str(tmp_path / f"{tool_name}-repo"),
            workspace="shared",
            tool_name=tool_name,
            working_directory=str(tmp_path),
            client_type=f"{tool_name}_adapter",
            client_session_id=f"{tool_name}-session",
        )
        session = adapter.start_session(context)
        assert session.session_id
        emitted = adapter.emit_event(
            session.session_id,
            SessionEvent(
                role_or_event_type="assistant_note",
                content=f"{tool_name} event: stable procedure",
                memory_type="procedure",
                title=f"{tool_name} procedure",
            ),
            context,
        )
        assert emitted.get("project_key") or emitted.get("ingested_memory")
        recall = adapter.request_recall("stable procedure", context)
        assert recall.combined_text
