from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from memory_agent_tool.logging import get_logger
from memory_agent_tool.models import (
    FeedbackRequest,
    MemoryRecallRequest,
    ProjectContext,
    SessionEvent,
    SessionStartRequest,
)

if TYPE_CHECKING:
    from memory_agent_tool.services import AppContainer

logger = get_logger("copilot_acp")


class CopilotACPHandler:
    def __init__(self, container: AppContainer):
        self.container = container

    def handle_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        action = payload.get("action", "")
        handler = {
            "start_session": self._start_session,
            "append_event": self._append_event,
            "end_session": self._end_session,
            "recall": self._recall,
            "feedback": self._feedback,
        }.get(action)
        if handler is None:
            return {"error": f"unknown action: {action}"}
        try:
            return handler(payload)
        except Exception as exc:
            logger.error("ACP request failed: %s %s", action, exc)
            return {"error": str(exc)}

    def _start_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        project = ProjectContext.model_validate(payload.get("project", {}))
        result = self.container.archive.start_session(
            SessionStartRequest(project=project, source_channel="copilot_acp")
        )
        return result.model_dump()

    def _append_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        session_id = payload["session_id"]
        event = SessionEvent.model_validate(payload.get("event", {}))
        project = ProjectContext.model_validate(payload.get("project", {}))
        result = self.container.archive.append_event(session_id, event, project)
        return result.model_dump()

    def _end_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        session_id = payload["session_id"]
        project = ProjectContext.model_validate(payload.get("project", {}))
        result = self.container.archive.end_session(session_id, project)
        return result.model_dump()

    def _recall(self, payload: dict[str, Any]) -> dict[str, Any]:
        project = ProjectContext.model_validate(payload.get("project", {}))
        query = payload.get("query", "")
        limit = payload.get("limit", 3)
        bundle = self.container.retrieval.recall(
            MemoryRecallRequest(project=project, query=query, limit=limit)
        )
        return bundle.model_dump()

    def _feedback(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = FeedbackRequest.model_validate(payload)
        result = self.container.conflicts.apply_feedback(request)
        return result.model_dump()
