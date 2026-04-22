from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from memory_agent_tool.logging import get_logger
from memory_agent_tool.models import (
    FeedbackRequest,
    MemoryIngestRequest,
    MemoryRecallRequest,
    ProjectContext,
    SessionEndResponse,
    SessionEvent,
    SessionStartRequest,
)

if TYPE_CHECKING:
    from memory_agent_tool.services import AppContainer

logger = get_logger("mcp")


class CodexMCPServer:
    def __init__(self, container: AppContainer):
        self.container = container

    def handle_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        handler = {
            "start_session": self._start_session,
            "append_event": self._append_event,
            "end_session": self._end_session,
            "ingest_memory": self._ingest_memory,
            "recall_memory": self._recall_memory,
            "apply_feedback": self._apply_feedback,
            "status_report": self._status_report,
            "health_check": self._health_check,
        }.get(tool_name)
        if handler is None:
            return {"error": f"unknown tool: {tool_name}"}
        try:
            return handler(arguments)
        except Exception as exc:
            logger.error("tool call failed: %s %s", tool_name, exc)
            return {"error": str(exc)}

    def _start_session(self, args: dict[str, Any]) -> dict[str, Any]:
        project = ProjectContext.model_validate(args.get("project", {}))
        request = SessionStartRequest(project=project, source_channel=args.get("source_channel", "mcp"))
        result = self.container.archive.start_session(request)
        return result.model_dump()

    def _append_event(self, args: dict[str, Any]) -> dict[str, Any]:
        session_id = args["session_id"]
        event = SessionEvent.model_validate(args.get("event", {}))
        project = ProjectContext.model_validate(args.get("project", {}))
        result = self.container.archive.append_event(session_id, event, project)
        return result.model_dump()

    def _end_session(self, args: dict[str, Any]) -> dict[str, Any]:
        session_id = args["session_id"]
        project = ProjectContext.model_validate(args.get("project", {}))
        result = self.container.archive.end_session(session_id, project)
        return result.model_dump()

    def _ingest_memory(self, args: dict[str, Any]) -> dict[str, Any]:
        request = MemoryIngestRequest.model_validate(args)
        resolved = self.container.projects.ensure_project(request.project)
        loaded_rules = self.container.rules_loader.load(request.project)
        ingested = self.container.memory.ingest(
            project_key=resolved.project_key,
            content=request.content,
            memory_type=request.memory_type,
            title=request.title,
            loaded_rules=loaded_rules,
            source_kind="mcp_ingest",
            source_session_id=request.source_session_id,
            source_message_id=request.source_message_id,
            recalled_from_memory=request.recalled_from_memory,
        )
        return ingested.model_dump()

    def _recall_memory(self, args: dict[str, Any]) -> dict[str, Any]:
        request = MemoryRecallRequest.model_validate(args)
        bundle = self.container.retrieval.recall(request)
        return bundle.model_dump()

    def _apply_feedback(self, args: dict[str, Any]) -> dict[str, Any]:
        request = FeedbackRequest.model_validate(args)
        result = self.container.conflicts.apply_feedback(request)
        return result.model_dump()

    def _status_report(self, _args: dict[str, Any]) -> dict[str, Any]:
        report = self.container.reporter.report()
        return report.model_dump()

    def _health_check(self, _args: dict[str, Any]) -> dict[str, Any]:
        health = self.container.reporter.health()
        return health.model_dump()
