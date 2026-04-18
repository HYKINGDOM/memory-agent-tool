from __future__ import annotations

from pathlib import Path

from memory_agent_tool.models import FeedbackRequest, MemoryRecallRequest, ProjectContext, SessionEvent, SessionStartRequest
from memory_agent_tool.services import AppContainer


def run_local_e2e(container: AppContainer, workspace: Path | None = None) -> dict:
    root = workspace or (container.settings.data_dir / "e2e-workspace")
    root.mkdir(parents=True, exist_ok=True)
    agents_path = root / "AGENTS.md"
    agents_path.write_text(
        "# Project rules\n\nAlways run tests with pytest -q.\n",
        encoding="utf-8",
    )
    context = ProjectContext(
        repo_identity=str(container.settings.root_dir),
        workspace="shared",
        tool_name="codex",
        working_directory=str(root),
    )
    codex = container.archive.start_session(SessionStartRequest(project=context, source_channel="e2e"))
    codex_fact = container.archive.append_event(
        codex.session_id,
        SessionEvent(
            role_or_event_type="assistant_note",
            content="API framework: FastAPI service.",
            source_tool="codex",
            memory_type="fact",
            title="api framework",
        ),
        context,
    )
    overlap = container.archive.append_event(
        codex.session_id,
        SessionEvent(
            role_or_event_type="assistant_note",
            content="Always run tests with pytest -q.",
            source_tool="codex",
            memory_type="procedure",
            title="test command",
        ),
        context,
    )
    trae_context = context.model_copy(update={"tool_name": "trae"})
    trae = container.archive.start_session(SessionStartRequest(project=trae_context, source_channel="e2e"))
    sqlite_event = container.archive.append_event(
        trae.session_id,
        SessionEvent(
            role_or_event_type="assistant_note",
            content="Database backend: sqlite.",
            source_tool="trae",
            memory_type="fact",
            title="database backend",
        ),
        trae_context,
    )
    conflict_event = container.archive.append_event(
        trae.session_id,
        SessionEvent(
            role_or_event_type="assistant_note",
            content="Database backend: postgres.",
            source_tool="trae",
            memory_type="fact",
            title="database backend",
        ),
        trae_context,
    )
    procedure_event = container.archive.append_event(
        trae.session_id,
        SessionEvent(
            role_or_event_type="assistant_note",
            content="Release procedure: run pytest -q, then memory-agent-tool report status.",
            source_tool="trae",
            memory_type="procedure",
            title="release procedure",
        ),
        trae_context,
    )
    container.conflicts.apply_feedback(FeedbackRequest(memory_id=codex_fact["ingested_memory"]["memory_id"], helpful=True))
    container.conflicts.apply_feedback(FeedbackRequest(memory_id=procedure_event["ingested_memory"]["memory_id"], helpful=True))
    container.conflicts.apply_feedback(FeedbackRequest(memory_id=procedure_event["ingested_memory"]["memory_id"], helpful=True))
    promoted = container.skills.promote(
        codex.resolved_project.project_key,
        procedure_event["ingested_memory"]["memory_id"],
    )
    copilot_context = context.model_copy(update={"tool_name": "copilot"})
    recall = container.retrieval.recall(
        MemoryRecallRequest(
            project=copilot_context,
            query="api framework database backend release procedure",
        )
    )
    report = {
        "project_key_shared": codex.resolved_project.project_key == trae.resolved_project.project_key,
        "overlap_blocked": overlap["ingested_memory"]["rule_overlap_state"] == "overlaps_agents",
        "conflict_not_pinned": conflict_event["ingested_memory"]["state"] == "conflict_candidate",
        "sqlite_pinned": sqlite_event["ingested_memory"]["state"] == "pinned_active",
        "fastapi_present": "FastAPI" in recall.combined_text,
        "sqlite_present": "sqlite" in recall.combined_text.lower(),
        "skill_promoted": promoted.name != "",
        "rules_prioritized": "pytest -q" in recall.combined_text,
    }
    report["status"] = "passed" if all(report.values()) else "failed"
    container.reporter.record_test_run("e2e-local", report["status"], report)
    return report
