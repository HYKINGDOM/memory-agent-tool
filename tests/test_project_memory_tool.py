from __future__ import annotations

from pathlib import Path

from memory_agent_tool.database import SCHEMA_VERSION
from memory_agent_tool.e2e import run_local_e2e
from memory_agent_tool.models import (
    FeedbackRequest,
    MemoryRecallRequest,
    ProjectContext,
    SessionEvent,
    SessionStartRequest,
    SkillPromotionRequest,
)
from memory_agent_tool.resolver import ProjectResolver
from memory_agent_tool.services import AppContainer


def make_context(tmp_path: Path, tool_name: str = "codex") -> ProjectContext:
    return ProjectContext(
        repo_identity=str(tmp_path / "repo"),
        workspace="shared",
        tool_name=tool_name,
        working_directory=str(tmp_path),
    )


def test_project_key_resolution_is_stable(tmp_path: Path):
    resolver = ProjectResolver()
    first = resolver.resolve(make_context(tmp_path, "codex"))
    second = resolver.resolve(make_context(tmp_path, "copilot"))
    assert first.project_key == second.project_key
    assert first.project_key.endswith("::shared")


def test_project_key_supports_optional_finer_scope_without_breaking_default_sharing(tmp_path: Path):
    resolver = ProjectResolver()
    shared = make_context(tmp_path, "codex")
    same_repo_other_tool = make_context(tmp_path, "copilot")
    fine_grained = ProjectContext(
        repo_identity=str(tmp_path / "repo"),
        workspace="shared",
        namespace="billing",
        branch="feature/memory-v2",
        tool_name="codex",
        working_directory=str(tmp_path / "packages" / "billing"),
    )
    shared_resolved = resolver.resolve(shared)
    same_repo_resolved = resolver.resolve(same_repo_other_tool)
    fine_resolved = resolver.resolve(fine_grained)
    assert shared_resolved.project_key == same_repo_resolved.project_key
    assert fine_resolved.project_key != shared_resolved.project_key
    assert fine_resolved.project_scope_metadata["namespace"] == "billing"
    assert fine_resolved.project_scope_metadata["branch"] == "feature/memory-v2"
    assert "scope_components" in fine_resolved.project_scope_metadata


def test_schema_initialization_with_fts5(container: AppContainer):
    row = container.db.fetchone(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='project_messages_fts'"
    )
    assert row is not None
    assert container.db.schema_version() == SCHEMA_VERSION


def test_session_search_groups_messages(container: AppContainer, tmp_path: Path):
    context = make_context(tmp_path)
    response = container.archive.start_session(SessionStartRequest(project=context))
    container.archive.append_event(
        response.session_id,
        SessionEvent(role_or_event_type="note", content="Deploy command: memory-agent-tool serve"),
        context,
    )
    container.archive.append_event(
        response.session_id,
        SessionEvent(role_or_event_type="note", content="Use uvicorn to run the FastAPI service"),
        context,
    )
    summaries = container.archive.search_sessions(response.resolved_project.project_key, "FastAPI service", limit=3)
    assert len(summaries) == 1
    assert "FastAPI" in summaries[0].summary


def test_durability_classification(container: AppContainer):
    assert container.memory.classify_durability("Traceback: boom", "fact").value == "transient"
    assert container.memory.classify_durability("Build command: pytest -q", "fact").value == "project_durable"


def test_rule_overlap_blocks_pinned_memory(container: AppContainer, tmp_path: Path):
    (tmp_path / "AGENTS.md").write_text("Always run tests with pytest -q.\n", encoding="utf-8")
    context = make_context(tmp_path)
    resolved = container.projects.ensure_project(context)
    loaded = container.rules_loader.load(context)
    ingested = container.memory.ingest(
        project_key=resolved.project_key,
        content="Always run tests with pytest -q.",
        memory_type="procedure",
        title="test command",
        loaded_rules=loaded,
        source_kind="direct",
    )
    assert ingested.state.value == "session_only"
    assert ingested.rule_overlap_state.value == "overlaps_agents"


def test_duplicate_rejected(container: AppContainer, tmp_path: Path):
    context = make_context(tmp_path)
    resolved = container.projects.ensure_project(context)
    loaded = container.rules_loader.load(context)
    first = container.memory.ingest(
        project_key=resolved.project_key,
        content="API framework: FastAPI",
        memory_type="fact",
        title="api framework",
        loaded_rules=loaded,
        source_kind="direct",
    )
    second = container.memory.ingest(
        project_key=resolved.project_key,
        content="API framework: FastAPI",
        memory_type="fact",
        title="api framework",
        loaded_rules=loaded,
        source_kind="direct",
    )
    assert first.memory_id == second.memory_id


def test_conflict_candidate_marked(container: AppContainer, tmp_path: Path):
    context = make_context(tmp_path)
    resolved = container.projects.ensure_project(context)
    loaded = container.rules_loader.load(context)
    container.memory.ingest(
        project_key=resolved.project_key,
        content="Database backend: sqlite",
        memory_type="fact",
        title="database backend",
        loaded_rules=loaded,
        source_kind="direct",
    )
    conflict = container.memory.ingest(
        project_key=resolved.project_key,
        content="Database backend: postgres",
        memory_type="fact",
        title="database backend",
        loaded_rules=loaded,
        source_kind="direct",
    )
    assert conflict.state.value == "conflict_candidate"
    assert conflict.conflict_state.value == "suspected"


def test_conflict_resolution_supersedes_weaker_memory_and_hides_it_from_recall(container: AppContainer, tmp_path: Path):
    context = make_context(tmp_path)
    resolved = container.projects.ensure_project(context)
    loaded = container.rules_loader.load(context)
    original = container.memory.ingest(
        project_key=resolved.project_key,
        content="Database backend: sqlite",
        memory_type="fact",
        title="database backend",
        loaded_rules=loaded,
        source_kind="direct",
    )
    container.conflicts.apply_feedback(FeedbackRequest(memory_id=original.memory_id, helpful=False))
    replacement = container.memory.ingest(
        project_key=resolved.project_key,
        content="Database backend: postgres",
        memory_type="fact",
        title="database backend",
        loaded_rules=loaded,
        source_kind="direct",
    )
    bundle = container.retrieval.recall(
        MemoryRecallRequest(project=context, query="database backend")
    )
    summaries = [item.summary.lower() for item in bundle.fixed_memory_summary]
    assert any("postgres" in summary for summary in summaries)
    assert not any("sqlite" in summary for summary in summaries)
    edges = container.db.fetchall(
        """
        SELECT relation_type
        FROM memory_edges
        WHERE project_key = ? AND from_memory_id = ? AND to_memory_id = ?
        """,
        (resolved.project_key, replacement.memory_id, original.memory_id),
    )
    assert {row["relation_type"] for row in edges} >= {"contradicts", "supersedes"}


def test_session_end_generates_focused_summary_and_extracts_durable_memory(container: AppContainer, tmp_path: Path):
    context = make_context(tmp_path)
    session = container.archive.start_session(SessionStartRequest(project=context))
    container.archive.append_event(
        session.session_id,
        SessionEvent(
            role_or_event_type="user",
            content="Please remember that the deployment command is memory-agent-tool serve.",
            capture_eligible=True,
            memory_type="procedure",
            title="deployment command",
        ),
        context,
    )
    ended = container.archive.end_session(session.session_id, context)
    assert ended["status"] == "ended"
    assert ended["focused_summary"]
    summary_row = container.db.fetchone(
        "SELECT summary FROM session_summaries WHERE project_key = ? AND session_id = ?",
        (session.resolved_project.project_key, session.session_id),
    )
    assert summary_row is not None
    assert "deployment" in summary_row["summary"].lower()
    bundle = container.retrieval.recall(
        MemoryRecallRequest(project=context, query="deployment command")
    )
    assert bundle.related_session_summaries
    assert "deployment" in bundle.related_session_summaries[0].summary.lower()


def test_provider_lite_prefetch_sync_and_session_end_hooks_are_persisted(container: AppContainer, tmp_path: Path):
    context = make_context(tmp_path)
    session = container.archive.start_session(SessionStartRequest(project=context))
    container.archive.append_event(
        session.session_id,
        SessionEvent(
            role_or_event_type="assistant_note",
            content="Release checklist: run pytest -q before report status.",
            capture_eligible=True,
            memory_type="procedure",
            title="release checklist",
        ),
        context,
    )
    container.archive.end_session(session.session_id, context)
    bundle = container.retrieval.recall(
        MemoryRecallRequest(project=context, query="release checklist pytest")
    )
    assert bundle.provider_context
    provider_rows = container.db.fetchall(
        "SELECT provider_name, payload_json FROM provider_events ORDER BY event_id ASC"
    )
    assert provider_rows
    assert any(row["provider_name"] == "supermemory_like" for row in provider_rows)
    assert any(row["provider_name"] == "holographic_like" for row in provider_rows)


def test_feedback_updates_trust_and_degrades(container: AppContainer, tmp_path: Path):
    context = make_context(tmp_path)
    resolved = container.projects.ensure_project(context)
    loaded = container.rules_loader.load(context)
    memory = container.memory.ingest(
        project_key=resolved.project_key,
        content="Repository uses FastAPI",
        memory_type="fact",
        title="framework",
        loaded_rules=loaded,
        source_kind="direct",
    )
    updated = container.conflicts.apply_feedback(FeedbackRequest(memory_id=memory.memory_id, helpful=False))
    updated = container.conflicts.apply_feedback(FeedbackRequest(memory_id=memory.memory_id, helpful=False))
    updated = container.conflicts.apply_feedback(FeedbackRequest(memory_id=memory.memory_id, helpful=False))
    assert updated.state.value == "degraded"
    assert updated.trust_score <= 0.2


def test_skill_promotion_requires_feedback(container: AppContainer, tmp_path: Path):
    context = make_context(tmp_path)
    resolved = container.projects.ensure_project(context)
    loaded = container.rules_loader.load(context)
    memory = container.memory.ingest(
        project_key=resolved.project_key,
        content="Release procedure: run pytest -q then memory-agent-tool report status",
        memory_type="procedure",
        title="release procedure",
        loaded_rules=loaded,
        source_kind="direct",
    )
    container.conflicts.apply_feedback(FeedbackRequest(memory_id=memory.memory_id, helpful=True))
    container.conflicts.apply_feedback(FeedbackRequest(memory_id=memory.memory_id, helpful=True))
    skill = container.skills.promote(resolved.project_key, memory.memory_id)
    assert skill.name.startswith("release")
    skill_file = Path(skill.file_path)
    assert skill_file.exists()


def test_context_bundle_prioritizes_pinned_memory_before_sessions(container: AppContainer, tmp_path: Path):
    context = make_context(tmp_path)
    resolved = container.projects.ensure_project(context)
    loaded = container.rules_loader.load(context)
    container.memory.ingest(
        project_key=resolved.project_key,
        content="API framework: FastAPI",
        memory_type="fact",
        title="api framework",
        loaded_rules=loaded,
        source_kind="direct",
    )
    response = container.archive.start_session(SessionStartRequest(project=context))
    container.archive.append_event(
        response.session_id,
        SessionEvent(role_or_event_type="note", content="Historical note about FastAPI migration"),
        context,
    )
    bundle = container.retrieval.recall(
        MemoryRecallRequest(project=context, query="api framework fastapi")
    )
    assert bundle.fixed_memory_summary
    assert "Pinned memory" in bundle.combined_text
    if bundle.related_session_summaries:
        assert bundle.combined_text.index("Pinned memory") < bundle.combined_text.index("Related sessions")


def test_recall_capture_guard_prevents_recursive_pollution(container: AppContainer, tmp_path: Path):
    context = make_context(tmp_path)
    response = container.archive.start_session(SessionStartRequest(project=context))
    result = container.archive.append_event(
        response.session_id,
        SessionEvent(
            role_or_event_type="note",
            content="Recovered memory: API framework is FastAPI",
            recalled_from_memory=True,
            memory_type="fact",
        ),
        context,
    )
    assert result["ingested_memory"]["state"] == "session_only"


def test_api_end_to_end_and_restart_persistence(client, settings: AppSettings):
    project = {
        "repo_identity": str(settings.root_dir / "repo"),
        "workspace": "shared",
        "tool_name": "codex",
        "working_directory": str(settings.root_dir),
    }
    session = client.post("/sessions/start", json={"project": project, "source_channel": "api"}).json()
    client.post(
        f"/sessions/{session['session_id']}/events",
        json={
            "project": project,
            "event": {
                "role_or_event_type": "note",
                "content": "API framework: FastAPI",
                "memory_type": "fact",
                "title": "api framework",
            },
        },
    )
    recall = client.post("/memory/recall", json={"project": project, "query": "api framework"}).json()
    assert "FastAPI" in recall["combined_text"]
    restarted = AppContainer.build(settings)
    bundle = restarted.retrieval.recall(
        MemoryRecallRequest(project=ProjectContext.model_validate(project), query="api framework")
    )
    assert "FastAPI" in bundle.combined_text


def test_full_local_e2e(container: AppContainer):
    report = run_local_e2e(container)
    assert report["status"] == "passed"
