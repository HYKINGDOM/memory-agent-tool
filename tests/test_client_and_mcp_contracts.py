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


def test_codex_mcp_tools_are_registered(container: AppContainer):
    tools = container.codex_mcp.list_tools()
    tool_names = {tool["name"] for tool in tools}
    assert {
        "project_memory_resolve",
        "project_memory_ingest",
        "project_memory_recall",
        "project_memory_feedback",
        "project_memory_status",
    }.issubset(tool_names)


def test_codex_mcp_round_trip(container: AppContainer, tmp_path: Path):
    context = make_context(tmp_path)
    resolved = container.codex_mcp.call_tool("project_memory_resolve", {"project": context.model_dump()})
    assert resolved["project_key"].endswith("::shared")

    session = container.archive.start_session(SessionStartRequest(project=context))
    event_result = container.codex_mcp.call_tool(
        "project_memory_ingest",
        {
            "project": context.model_dump(),
            "session_id": session.session_id,
            "event": {
                "role_or_event_type": "assistant_note",
                "content": "API framework: FastAPI",
                "memory_type": "fact",
                "title": "api framework",
            },
        },
    )
    assert event_result["ingested_memory"]["state"] == "pinned_active"

    recall = container.codex_mcp.call_tool(
        "project_memory_recall",
        {"project": context.model_dump(), "query": "api framework"},
    )
    assert "FastAPI" in recall["combined_text"]

    feedback = container.codex_mcp.call_tool(
        "project_memory_feedback",
        {"memory_id": event_result["ingested_memory"]["memory_id"], "helpful": True},
    )
    assert feedback["trust_score"] > 0.6

    status = container.codex_mcp.call_tool("project_memory_status", {})
    assert status["service_health"] == "ok"


def test_trae_and_copilot_fake_adapters_follow_contract(container: AppContainer, tmp_path: Path):
    for adapter_name in ("trae", "copilot"):
        adapter = container.client_registry.get(adapter_name)
        context = ProjectContext(
            repo_identity=str(tmp_path / f"{adapter_name}-repo"),
            workspace="shared",
            tool_name=adapter_name,
            working_directory=str(tmp_path),
            client_type=f"{adapter_name}_adapter",
            client_session_id=f"{adapter_name}-session",
        )
        identified = adapter.identify_project(context)
        assert identified.project_key.endswith("::shared")
        session = adapter.start_session(context)
        emitted = adapter.emit_event(
            session.session_id,
            SessionEvent(
                role_or_event_type="assistant_note",
                content=f"{adapter_name} event: stable procedure",
                memory_type="procedure",
                title=f"{adapter_name} procedure",
            ),
        )
        assert emitted["project_key"] == identified.project_key
        recall = adapter.request_recall("stable procedure", context)
        assert recall.combined_text
        if emitted["ingested_memory"]:
            feedback = adapter.submit_feedback(
                FeedbackRequest(memory_id=emitted["ingested_memory"]["memory_id"], helpful=True)
            )
            assert feedback.trust_score >= 0.6
