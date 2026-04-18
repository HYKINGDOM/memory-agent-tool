from __future__ import annotations

from pathlib import Path

from memory_agent_tool.models import FeedbackRequest, MemoryRecallRequest, ProjectContext, SessionEvent, SessionStartRequest
from memory_agent_tool.services import AppContainer


def make_context(tmp_path: Path, tool_name: str = "codex") -> ProjectContext:
    return ProjectContext(
        repo_identity=str(tmp_path / "repo"),
        workspace="shared",
        tool_name=tool_name,
        working_directory=str(tmp_path),
    )


def test_recall_ranks_high_trust_high_relevance_pinned_memory_first(container: AppContainer, tmp_path: Path):
    context = make_context(tmp_path)
    resolved = container.projects.ensure_project(context)
    loaded = container.rules_loader.load(context)
    low = container.memory.ingest(
        project_key=resolved.project_key,
        content="Database backend: sqlite for local smoke tests only",
        memory_type="fact",
        title="database backend",
        loaded_rules=loaded,
        source_kind="direct",
    )
    high = container.memory.ingest(
        project_key=resolved.project_key,
        content="Primary database backend: postgres with logical replication",
        memory_type="fact",
        title="primary database backend",
        loaded_rules=loaded,
        source_kind="direct",
    )
    container.conflicts.apply_feedback(FeedbackRequest(memory_id=high.memory_id, helpful=True))
    bundle = container.retrieval.recall(
        MemoryRecallRequest(project=context, query="primary postgres database")
    )
    assert bundle.fixed_memory_summary
    assert "postgres" in bundle.fixed_memory_summary[0].summary.lower()


def test_recall_does_not_let_degraded_or_superseded_memory_outrank_active_memory(container: AppContainer, tmp_path: Path):
    context = make_context(tmp_path)
    resolved = container.projects.ensure_project(context)
    loaded = container.rules_loader.load(context)
    original = container.memory.ingest(
        project_key=resolved.project_key,
        content="Deployment command: uvicorn app:app",
        memory_type="fact",
        title="deployment command",
        loaded_rules=loaded,
        source_kind="direct",
    )
    container.conflicts.apply_feedback(FeedbackRequest(memory_id=original.memory_id, helpful=False))
    replacement = container.memory.ingest(
        project_key=resolved.project_key,
        content="Deployment command: memory-agent-tool serve",
        memory_type="fact",
        title="deployment command",
        loaded_rules=loaded,
        source_kind="direct",
    )
    bundle = container.retrieval.recall(
        MemoryRecallRequest(project=context, query="deployment command")
    )
    summaries = [item.summary.lower() for item in bundle.fixed_memory_summary]
    assert any("memory-agent-tool serve" in summary for summary in summaries)
    assert not any("uvicorn app:app" in summary for summary in summaries)
    assert replacement.memory_id != original.memory_id


def test_combined_text_orders_rules_then_memory_then_sessions_then_skills_then_provider(container: AppContainer, tmp_path: Path):
    (tmp_path / "AGENTS.md").write_text("Use memory-agent-tool serve for local startup.\n", encoding="utf-8")
    context = make_context(tmp_path)
    resolved = container.projects.ensure_project(context)
    loaded = container.rules_loader.load(context)
    memory = container.memory.ingest(
        project_key=resolved.project_key,
        content="Startup command: memory-agent-tool serve",
        memory_type="procedure",
        title="startup command",
        loaded_rules=loaded,
        source_kind="direct",
    )
    container.conflicts.apply_feedback(FeedbackRequest(memory_id=memory.memory_id, helpful=True))
    container.conflicts.apply_feedback(FeedbackRequest(memory_id=memory.memory_id, helpful=True))
    container.skills.promote(resolved.project_key, memory.memory_id)
    session = container.archive.start_session(SessionStartRequest(project=context))
    container.archive.append_event(
        session.session_id,
        SessionEvent(
            role_or_event_type="assistant_note",
            content="Historical startup note: the CLI wrapper superseded raw uvicorn commands.",
            capture_eligible=True,
            memory_type="fact",
            title="startup note",
        ),
        context,
    )
    container.archive.end_session(session.session_id, context)
    bundle = container.retrieval.recall(
        MemoryRecallRequest(project=context, query="startup command")
    )
    text = bundle.combined_text
    assert "Rules:" in text
    assert "Pinned memory:" in text
    assert "Related sessions:" in text
    assert "Recommended skills:" in text
    assert "Provider context:" in text
    assert text.index("Rules:") < text.index("Pinned memory:")
    assert text.index("Pinned memory:") < text.index("Related sessions:")
    assert text.index("Related sessions:") < text.index("Recommended skills:")
    assert text.index("Recommended skills:") < text.index("Provider context:")


def test_conflict_hints_only_surface_when_query_is_related(container: AppContainer, tmp_path: Path):
    context = make_context(tmp_path)
    resolved = container.projects.ensure_project(context)
    loaded = container.rules_loader.load(context)
    container.memory.ingest(
        project_key=resolved.project_key,
        content="Project runtime command: python",
        memory_type="fact",
        title="runtime",
        loaded_rules=loaded,
        source_kind="direct",
    )
    container.memory.ingest(
        project_key=resolved.project_key,
        content="Project runtime command: pypy",
        memory_type="fact",
        title="runtime",
        loaded_rules=loaded,
        source_kind="direct",
    )
    unrelated = container.retrieval.recall(
        MemoryRecallRequest(project=context, query="frontend css")
    )
    related = container.retrieval.recall(
        MemoryRecallRequest(project=context, query="runtime")
    )
    assert not unrelated.conflict_hints
    assert related.conflict_hints
