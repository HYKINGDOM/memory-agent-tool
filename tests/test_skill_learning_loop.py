from __future__ import annotations

from pathlib import Path

from memory_agent_tool.models import FeedbackRequest, MemoryRecallRequest, ProjectContext
from memory_agent_tool.services import AppContainer


def make_context(tmp_path: Path, tool_name: str = "codex") -> ProjectContext:
    return ProjectContext(
        repo_identity=str(tmp_path / "repo"),
        workspace="shared",
        tool_name=tool_name,
        working_directory=str(tmp_path),
    )


def test_skill_feedback_refreshes_skill_and_bumps_version(container: AppContainer, tmp_path: Path):
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
    first_row = container.db.fetchone("SELECT version FROM project_skills WHERE skill_id = ?", (skill.skill_id,))
    container.skills.record_skill_feedback(skill.skill_id, helpful=True, accepted=True)
    container.skills.refresh_skill_from_sources(skill.skill_id)
    second_row = container.db.fetchone("SELECT version, last_refreshed_at FROM project_skills WHERE skill_id = ?", (skill.skill_id,))
    assert int(second_row["version"]) > int(first_row["version"])
    assert second_row["last_refreshed_at"] is not None


def test_superseded_source_memory_marks_skill_for_refresh(container: AppContainer, tmp_path: Path):
    context = make_context(tmp_path)
    resolved = container.projects.ensure_project(context)
    loaded = container.rules_loader.load(context)
    original = container.memory.ingest(
        project_key=resolved.project_key,
        content="Deployment command: uvicorn app:app",
        memory_type="procedure",
        title="deployment command",
        loaded_rules=loaded,
        source_kind="direct",
    )
    container.conflicts.apply_feedback(FeedbackRequest(memory_id=original.memory_id, helpful=True))
    container.conflicts.apply_feedback(FeedbackRequest(memory_id=original.memory_id, helpful=True))
    skill = container.skills.promote(resolved.project_key, original.memory_id)
    container.conflicts.apply_feedback(FeedbackRequest(memory_id=original.memory_id, helpful=False))
    replacement = container.memory.ingest(
        project_key=resolved.project_key,
        content="Deployment command: memory-agent-tool serve",
        memory_type="procedure",
        title="deployment command",
        loaded_rules=loaded,
        source_kind="direct",
    )
    container.conflicts.apply_feedback(FeedbackRequest(memory_id=replacement.memory_id, helpful=True))
    container.conflicts.apply_feedback(FeedbackRequest(memory_id=replacement.memory_id, helpful=True))
    container.skills.refresh_skill_from_sources(skill.skill_id)
    row = container.db.fetchone("SELECT status FROM project_skills WHERE skill_id = ?", (skill.skill_id,))
    assert row["status"] in {"active", "candidate_refresh"}


def test_skill_recall_contains_rationale_and_feedback_signal(container: AppContainer, tmp_path: Path):
    context = make_context(tmp_path)
    resolved = container.projects.ensure_project(context)
    loaded = container.rules_loader.load(context)
    memory = container.memory.ingest(
        project_key=resolved.project_key,
        content="Startup checklist: run memory-agent-tool serve and verify provider status",
        memory_type="procedure",
        title="startup checklist",
        loaded_rules=loaded,
        source_kind="direct",
    )
    container.conflicts.apply_feedback(FeedbackRequest(memory_id=memory.memory_id, helpful=True))
    container.conflicts.apply_feedback(FeedbackRequest(memory_id=memory.memory_id, helpful=True))
    skill = container.skills.promote(resolved.project_key, memory.memory_id)
    container.skills.record_skill_feedback(skill.skill_id, helpful=True, accepted=True)
    bundle = container.retrieval.recall(
        MemoryRecallRequest(project=context, query="startup checklist")
    )
    assert bundle.recommended_skills
    assert bundle.recommended_skills[0].rationale
