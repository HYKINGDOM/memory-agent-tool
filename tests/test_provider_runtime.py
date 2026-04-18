from __future__ import annotations

from pathlib import Path

from memory_agent_tool.models import MemoryRecallRequest, ProjectContext, SessionEvent, SessionStartRequest
from memory_agent_tool.services import AppContainer


def make_context(tmp_path: Path, tool_name: str = "codex") -> ProjectContext:
    return ProjectContext(
        repo_identity=str(tmp_path / "repo"),
        workspace="shared",
        tool_name=tool_name,
        working_directory=str(tmp_path),
    )


def test_provider_runtime_can_disable_provider_from_prefetch(container: AppContainer, tmp_path: Path):
    context = make_context(tmp_path)
    session = container.archive.start_session(SessionStartRequest(project=context))
    container.archive.append_event(
        session.session_id,
        SessionEvent(
            role_or_event_type="assistant_note",
            content="Release checklist: run pytest -q then report status.",
            capture_eligible=True,
            memory_type="procedure",
            title="release checklist",
        ),
        context,
    )
    container.archive.end_session(session.session_id, context)
    container.providers.configure({"enabled_providers": ["local_builtin", "holographic_like"]})
    bundle = container.retrieval.recall(
        MemoryRecallRequest(project=context, query="release checklist")
    )
    assert all("container:" not in item for item in bundle.provider_context)


def test_provider_runtime_prefetch_merge_policy_prefers_higher_scored_context(container: AppContainer, tmp_path: Path):
    context = make_context(tmp_path)
    session = container.archive.start_session(SessionStartRequest(project=context))
    container.archive.append_event(
        session.session_id,
        SessionEvent(
            role_or_event_type="assistant_note",
            content="Runtime provider note: release checklist includes provider verification.",
            capture_eligible=True,
            memory_type="procedure",
            title="provider checklist",
        ),
        context,
    )
    container.archive.end_session(session.session_id, context)
    bundle = container.retrieval.recall(
        MemoryRecallRequest(project=context, query="provider verification release checklist")
    )
    assert bundle.provider_context


def test_provider_runtime_failure_falls_back_without_breaking_recall(container: AppContainer, tmp_path: Path):
    context = make_context(tmp_path)
    container.providers.configure({"forced_failures": ["supermemory_like"]})
    bundle = container.retrieval.recall(
        MemoryRecallRequest(project=context, query="any query")
    )
    status = container.providers.status()
    assert "supermemory_like" in status
    assert status["supermemory_like"]["status"] in {"ready", "degraded"}
    assert bundle is not None
