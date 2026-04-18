from __future__ import annotations

from pathlib import Path

from memory_agent_tool.models import FeedbackRequest, ProjectContext
from memory_agent_tool.services import AppContainer


def make_context(tmp_path: Path, tool_name: str = "codex") -> ProjectContext:
    return ProjectContext(
        repo_identity=str(tmp_path / "repo"),
        workspace="shared",
        tool_name=tool_name,
        working_directory=str(tmp_path),
    )


def test_review_stale_memories_marks_low_trust_old_memory_for_review(container: AppContainer, tmp_path: Path):
    context = make_context(tmp_path)
    resolved = container.projects.ensure_project(context)
    loaded = container.rules_loader.load(context)
    memory = container.memory.ingest(
        project_key=resolved.project_key,
        content="Legacy build command: python app.py",
        memory_type="fact",
        title="legacy build command",
        loaded_rules=loaded,
        source_kind="direct",
    )
    container.conflicts.apply_feedback(FeedbackRequest(memory_id=memory.memory_id, helpful=False))
    container.db.execute(
        "UPDATE memory_items SET last_verified_at = ?, updated_at = ? WHERE memory_id = ?",
        (1.0, 1.0, memory.memory_id),
    )
    result = container.maintenance.review_stale_memories(resolved.project_key)
    assert result["review_candidates"] >= 1
    row = container.db.fetchone("SELECT state FROM memory_items WHERE memory_id = ?", (memory.memory_id,))
    assert row["state"] in {"degraded", "session_only"}


def test_consolidate_project_memory_merges_similar_entries_and_preserves_lineage(container: AppContainer, tmp_path: Path):
    context = make_context(tmp_path)
    resolved = container.projects.ensure_project(context)
    loaded = container.rules_loader.load(context)
    first = container.memory.ingest(
        project_key=resolved.project_key,
        content="Release procedure: run pytest -q then report status",
        memory_type="procedure",
        title="release procedure",
        loaded_rules=loaded,
        source_kind="direct",
    )
    second = container.memory.ingest(
        project_key=resolved.project_key,
        content="Release procedure: run pytest -q, report status, then verify providers",
        memory_type="procedure",
        title="release procedure",
        loaded_rules=loaded,
        source_kind="direct",
    )
    result = container.maintenance.consolidate_project_memory(resolved.project_key)
    assert result["consolidated"] >= 1
    edges = container.db.fetchall(
        """
        SELECT relation_type
        FROM memory_edges
        WHERE project_key = ? AND from_memory_id = ? AND to_memory_id = ?
        """,
        (resolved.project_key, second.memory_id, first.memory_id),
    )
    assert any(row["relation_type"] == "supersedes" for row in edges)
