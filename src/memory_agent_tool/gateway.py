from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from memory_agent_tool.logging import get_logger
from memory_agent_tool.models import (
    FeedbackRequest,
    IngestedMemory,
    MemoryRecallRequest,
    ProjectContext,
    SessionEvent,
    SessionStartRequest,
    SessionStartResponse,
)

if TYPE_CHECKING:
    from memory_agent_tool.services import AppContainer

logger = get_logger("gateway")


class ClientAdapter:
    def mount_project_memory_server(self, context: ProjectContext) -> dict[str, Any]:
        raise NotImplementedError

    def start_session(self, context: ProjectContext) -> SessionStartResponse:
        raise NotImplementedError

    def emit_event(self, session_id: str, event: SessionEvent, context: ProjectContext) -> dict[str, Any]:
        raise NotImplementedError

    def request_recall(self, query: str, context: ProjectContext):
        raise NotImplementedError

    def submit_feedback(self, request: FeedbackRequest):
        raise NotImplementedError

    def handshake(self, context: ProjectContext) -> dict[str, Any]:
        raise NotImplementedError


class CopilotRealAdapter(ClientAdapter):
    def __init__(self, container: AppContainer):
        self.container = container

    def mount_project_memory_server(self, context: ProjectContext) -> dict[str, Any]:
        return {"status": "mounted", "reused": False}

    def start_session(self, context: ProjectContext) -> SessionStartResponse:
        return self.container.archive.start_session(
            SessionStartRequest(project=context, source_channel="copilot_acp")
        )

    def emit_event(self, session_id: str, event: SessionEvent, context: ProjectContext) -> dict[str, Any]:
        result = self.container.archive.append_event(session_id, event, context)
        return result.model_dump()

    def request_recall(self, query: str, context: ProjectContext):
        return self.container.retrieval.recall(
            MemoryRecallRequest(project=context, query=query)
        )

    def submit_feedback(self, request: FeedbackRequest) -> IngestedMemory:
        return self.container.conflicts.apply_feedback(request)

    def handshake(self, context: ProjectContext) -> dict[str, Any]:
        resolved = self.container.projects.ensure_project(context)
        return {
            "agent_name": "memory-agent-tool",
            "session_id": f"copilot-acp-{resolved.project_key}",
            "project_key": resolved.project_key,
        }


class TraeRealAdapter(ClientAdapter):
    def __init__(self, container: AppContainer):
        self.container = container

    def mount_project_memory_server(self, context: ProjectContext) -> dict[str, Any]:
        return {"status": "mounted", "reused": False}

    def open_chat_session(self, context: ProjectContext, prompt: str) -> dict[str, Any]:
        return {
            "status": "chat_opened",
            "mount": {"status": "mounted", "reused": False},
            "prompt": prompt,
        }

    def start_session(self, context: ProjectContext) -> SessionStartResponse:
        return self.container.archive.start_session(
            SessionStartRequest(project=context, source_channel="trae_cli")
        )

    def emit_event(self, session_id: str, event: SessionEvent, context: ProjectContext) -> dict[str, Any]:
        result = self.container.archive.append_event(session_id, event, context)
        return result.model_dump()

    def request_recall(self, query: str, context: ProjectContext):
        return self.container.retrieval.recall(
            MemoryRecallRequest(project=context, query=query)
        )

    def submit_feedback(self, request: FeedbackRequest) -> IngestedMemory:
        return self.container.conflicts.apply_feedback(request)

    def handshake(self, context: ProjectContext) -> dict[str, Any]:
        resolved = self.container.projects.ensure_project(context)
        return {
            "agent_name": "memory-agent-tool",
            "session_id": f"trae-cli-{resolved.project_key}",
            "project_key": resolved.project_key,
        }


class ClientRegistry:
    def __init__(self, container: AppContainer):
        self.container = container
        self._adapters: dict[str, ClientAdapter] = {
            "copilot_real": CopilotRealAdapter(container),
            "trae_real": TraeRealAdapter(container),
        }

    def get(self, name: str) -> ClientAdapter:
        if name not in self._adapters:
            raise KeyError(f"unknown client adapter: {name}")
        return self._adapters[name]

    def list_adapters(self) -> list[str]:
        return list(self._adapters.keys())
