from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from memory_agent_tool.app import create_app
from memory_agent_tool.config import AppSettings
from memory_agent_tool.models import MemoryRecallRequest, ProjectContext, SessionEvent, SessionStartRequest
from memory_agent_tool.services import AppContainer


def make_context(tmp_path: Path, tool_name: str = "codex", client_type: str = "codex_mcp") -> ProjectContext:
    return ProjectContext(
        repo_identity=str(tmp_path / "repo"),
        workspace="shared",
        tool_name=tool_name,
        working_directory=str(tmp_path),
        client_type=client_type,
        client_session_id=f"{tool_name}-session",
    )


def test_extended_schema_tables_exist(container: AppContainer):
    expected = {
        "client_connections",
        "provider_runs",
        "memory_conflicts",
        "session_summaries",
        "service_state",
    }
    rows = container.db.fetchall(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
    )
    existing = {row["name"] for row in rows}
    assert expected.issubset(existing)


def test_session_summary_cache_is_created_and_reused(container: AppContainer, tmp_path: Path):
    context = make_context(tmp_path)
    session = container.archive.start_session(SessionStartRequest(project=context))
    container.archive.append_event(
        session.session_id,
        SessionEvent(
            role_or_event_type="assistant_note",
            content="Deployment summary: use memory-agent-tool serve for the API service.",
            memory_type="fact",
            title="deployment summary",
        ),
        context,
    )
    first = container.retrieval.recall(
        MemoryRecallRequest(project=context, query="deployment api service")
    )
    summaries = container.db.fetchall(
        "SELECT session_id, summary FROM session_summaries WHERE project_key = ?",
        (session.resolved_project.project_key,),
    )
    assert summaries
    second = container.retrieval.recall(
        MemoryRecallRequest(project=context, query="deployment api service")
    )
    assert first.related_session_summaries
    assert second.related_session_summaries
    assert summaries[0]["session_id"] == session.session_id


def test_conflict_record_persists_resolution_chain(container: AppContainer, tmp_path: Path):
    context = make_context(tmp_path)
    resolved = container.projects.ensure_project(context)
    loaded = container.rules_loader.load(context)
    first = container.memory.ingest(
        project_key=resolved.project_key,
        content="Database backend: sqlite",
        memory_type="fact",
        title="database backend",
        loaded_rules=loaded,
        source_kind="direct",
    )
    second = container.memory.ingest(
        project_key=resolved.project_key,
        content="Database backend: postgres",
        memory_type="fact",
        title="database backend",
        loaded_rules=loaded,
        source_kind="direct",
    )
    conflicts = container.db.fetchall(
        "SELECT existing_memory_id, candidate_memory_id, resolution FROM memory_conflicts WHERE project_key = ?",
        (resolved.project_key,),
    )
    assert conflicts
    assert conflicts[0]["existing_memory_id"] == first.memory_id
    assert conflicts[0]["candidate_memory_id"] == second.memory_id


def test_provider_registry_tracks_provider_runs(container: AppContainer):
    status = container.providers.status()
    assert "local_builtin" in status
    rows = container.db.fetchall("SELECT provider_name, status FROM provider_runs")
    assert rows
    assert any(row["provider_name"] == "local_builtin" for row in rows)


def test_client_connection_is_recorded_on_session_start(container: AppContainer, tmp_path: Path):
    context = make_context(tmp_path, tool_name="copilot", client_type="copilot_adapter")
    session = container.archive.start_session(SessionStartRequest(project=context))
    rows = container.db.fetchall(
        "SELECT client_type, session_id FROM client_connections WHERE session_id = ?",
        (session.session_id,),
    )
    assert rows
    assert rows[0]["client_type"] == "copilot_adapter"


def test_provider_status_and_doctor_endpoints(client: TestClient):
    provider_status = client.get("/providers/status")
    assert provider_status.status_code == 200
    payload = provider_status.json()
    assert "providers" in payload

    provider_observability = client.get("/providers/observability")
    assert provider_observability.status_code == 200
    provider_payload = provider_observability.json()
    assert "policy" in provider_payload
    assert "statuses" in provider_payload

    doctor = client.post("/doctor/check")
    assert doctor.status_code == 200
    doctor_payload = doctor.json()
    assert doctor_payload["service_health"] == "ok"


def test_session_and_project_listing_endpoints(client: TestClient, settings: AppSettings):
    project = {
        "repo_identity": str(settings.root_dir / "repo"),
        "workspace": "shared",
        "tool_name": "codex",
        "working_directory": str(settings.root_dir),
        "client_type": "codex_mcp",
        "client_session_id": "codex-session",
    }
    session = client.post("/sessions/start", json={"project": project, "source_channel": "api"}).json()
    client.post(
        f"/sessions/{session['session_id']}/events",
        json={
            "project": project,
            "event": {
                "role_or_event_type": "note",
                "content": "Repository framework: FastAPI",
                "memory_type": "fact",
                "title": "framework",
            },
        },
    )
    project_key = session["resolved_project"]["project_key"]
    assert client.get(f"/sessions/{session['session_id']}").status_code == 200
    assert client.get(f"/projects/{project_key}/memory").status_code == 200
    assert client.get(f"/projects/{project_key}/skills").status_code == 200
    assert client.get(f"/projects/{project_key}/conflicts").status_code == 200
    scope_response = client.get(f"/projects/{project_key}/scope")
    assert scope_response.status_code == 200
    scope_payload = scope_response.json()
    assert scope_payload["project"]["project_key"] == project_key


def test_rebuild_summaries_endpoint(client: TestClient):
    response = client.post("/summaries/rebuild")
    assert response.status_code == 200
    payload = response.json()
    assert "rebuilt" in payload


def test_maintenance_and_skill_endpoints_expose_fourth_phase_services(client: TestClient, settings: AppSettings):
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
                "content": "Release procedure: run pytest -q then memory-agent-tool report status",
                "memory_type": "procedure",
                "title": "release procedure",
            },
        },
    )
    project_key = session["resolved_project"]["project_key"]
    rebuild = client.post("/summaries/rebuild")
    assert rebuild.status_code == 200
    review = client.post(f"/maintenance/review-stale/{project_key}")
    assert review.status_code == 200
    consolidate = client.post(f"/maintenance/consolidate/{project_key}")
    assert consolidate.status_code == 200


def test_status_report_includes_provider_skill_and_alias_observability(container: AppContainer, tmp_path: Path):
    context = make_context(tmp_path)
    resolved = container.projects.ensure_project(context)
    container.projects.register_alias("legacy::shared", resolved.project_key)
    report = container.reporter.report()
    assert "policy" in report.provider_observability
    assert "total_skills" in report.skill_observability
    assert "alias_count" in report.project_scope_observability
