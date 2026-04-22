from __future__ import annotations

from fastapi import FastAPI, HTTPException

from memory_agent_tool.config import AppSettings
from memory_agent_tool.e2e import run_local_e2e
from memory_agent_tool.models import (
    FeedbackRequest,
    HealthResponse,
    MemoryIngestRequest,
    MemoryRecallRequest,
    ProjectAliasRequest,
    ProjectContext,
    SessionEvent,
    SessionEndResponse,
    SessionStartRequest,
    SessionStartResponse,
    SkillFeedbackRequest,
    SkillPromotionRequest,
    StatusReport,
)
from memory_agent_tool.services import AppContainer


def create_app(settings: AppSettings | None = None) -> FastAPI:
    resolved_settings = settings or AppSettings.from_env()
    container = AppContainer.build(resolved_settings)
    app = FastAPI(title="memory-agent-tool", version="0.1.0")
    app.state.container = container

    @app.post("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return container.reporter.health()

    @app.post("/projects/resolve")
    def resolve_project(project: ProjectContext):
        return container.projects.ensure_project(project)

    @app.post("/sessions/start", response_model=SessionStartResponse)
    def start_session(request: SessionStartRequest) -> SessionStartResponse:
        return container.archive.start_session(request)

    @app.post("/sessions/{session_id}/events")
    def append_event(session_id: str, payload: dict):
        event = SessionEvent.model_validate(payload["event"])
        project = ProjectContext.model_validate(payload["project"])
        try:
            result = container.archive.append_event(session_id, event, project)
            return result.model_dump()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/sessions/{session_id}/end", response_model=SessionEndResponse)
    def end_session(session_id: str, project: ProjectContext):
        try:
            return container.archive.end_session(session_id, project)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/memory/ingest")
    def ingest_memory(request: MemoryIngestRequest):
        resolved = container.projects.ensure_project(request.project)
        loaded_rules = container.rules_loader.load(request.project)
        ingested = container.memory.ingest(
            project_key=resolved.project_key,
            content=request.content,
            memory_type=request.memory_type,
            title=request.title,
            loaded_rules=loaded_rules,
            source_kind="direct_ingest",
            source_session_id=request.source_session_id,
            source_message_id=request.source_message_id,
            recalled_from_memory=request.recalled_from_memory,
        )
        return ingested

    @app.post("/memory/recall")
    def recall_memory(request: MemoryRecallRequest):
        return container.retrieval.recall(request)

    @app.post("/memory/feedback")
    def apply_feedback(request: FeedbackRequest):
        try:
            return container.conflicts.apply_feedback(request)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/skills/promote")
    def promote_skill(request: SkillPromotionRequest):
        resolved = container.projects.ensure_project(request.project)
        try:
            if request.memory_id is not None:
                return container.skills.promote(
                    resolved.project_key, request.memory_id, request.min_positive_feedback
                )
            return container.skills.auto_promote(resolved.project_key, request.min_positive_feedback)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/skills/{skill_id}/feedback")
    def record_skill_feedback(skill_id: int, request: SkillFeedbackRequest):
        try:
            return container.skills.record_skill_feedback(
                skill_id,
                helpful=request.helpful,
                accepted=request.accepted,
            ).model_dump()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/skills/{skill_id}/refresh")
    def refresh_skill(skill_id: int):
        try:
            return container.skills.refresh_skill_from_sources(skill_id)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/status/report", response_model=StatusReport)
    def status_report() -> StatusReport:
        return container.reporter.report()

    @app.get("/providers/status")
    def providers_status():
        return {"providers": container.providers.status()}

    @app.get("/providers/observability")
    def providers_observability():
        return container.providers.observability_summary()

    @app.post("/providers/config")
    def configure_providers(payload: dict):
        return container.providers.configure(payload)

    @app.post("/providers/{provider_name}/reload")
    def reload_provider(provider_name: str):
        status = container.providers.reload(
            provider_name,
            root_dir=str(resolved_settings.root_dir),
            db_path=str(resolved_settings.db_path),
        )
        return {
            "provider_name": status.provider_name,
            "status": status.status,
            "capabilities": status.capabilities,
            "last_error": status.last_error,
        }

    @app.get("/sessions/{session_id}")
    def get_session(session_id: str):
        row = container.db.fetchone("SELECT * FROM project_sessions WHERE session_id = ?", (session_id,))
        if row is None:
            raise HTTPException(status_code=404, detail="session not found")
        return dict(row)

    @app.get("/projects/{project_key}/memory")
    def get_project_memory(project_key: str):
        return {"items": container.memory.list_active(project_key)}

    @app.get("/projects/{project_key}/skills")
    def get_project_skills(project_key: str):
        return {"items": [item.model_dump() for item in container.skills.relevant_skills(project_key, "")]}

    @app.get("/projects/{project_key}/scope")
    def get_project_scope(project_key: str):
        project = container.db.fetchone(
            """
            SELECT project_key, canonical_project_key, repo_identity, namespace, workspace, branch, monorepo_subpath,
                   scope_components_json, updated_at
            FROM projects
            WHERE project_key = ?
            """,
            (project_key,),
        )
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")
        aliases = container.db.fetchall(
            """
            SELECT alias_key, canonical_project_key, created_at
            FROM project_aliases
            WHERE canonical_project_key = ? OR alias_key = ?
            ORDER BY created_at DESC
            """,
            (project["canonical_project_key"] or project_key, project_key),
        )
        return {"project": dict(project), "aliases": [dict(row) for row in aliases]}

    @app.post("/projects/aliases")
    def register_project_alias(request: ProjectAliasRequest):
        container.projects.register_alias(request.alias_key, request.canonical_project_key)
        return {"alias_key": request.alias_key, "canonical_project_key": request.canonical_project_key}

    @app.get("/projects/{project_key}/conflicts")
    def get_project_conflicts(project_key: str):
        rows = container.db.fetchall(
            """
            SELECT conflict_id, project_key, existing_memory_id, candidate_memory_id, resolution, reason
            FROM memory_conflicts
            WHERE project_key = ?
            ORDER BY created_at DESC
            """,
            (project_key,),
        )
        return {"items": [dict(row) for row in rows]}

    @app.post("/summaries/rebuild")
    def rebuild_summaries():
        return container.maintenance.rebuild_session_summaries().model_dump()

    @app.post("/maintenance/review-stale/{project_key}")
    def review_stale(project_key: str):
        return container.maintenance.review_stale_memories(project_key).model_dump()

    @app.post("/maintenance/consolidate/{project_key}")
    def consolidate_project_memory(project_key: str):
        return container.maintenance.consolidate_project_memory(project_key).model_dump()

    @app.post("/doctor/check", response_model=StatusReport)
    def doctor_check() -> StatusReport:
        return container.reporter.report()

    @app.post("/test/e2e-local")
    def execute_local_e2e():
        return run_local_e2e(container)

    return app
