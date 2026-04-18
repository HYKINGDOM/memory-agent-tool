from __future__ import annotations

from memory_agent_tool.models import FeedbackRequest, MemoryRecallRequest, ProjectContext, SessionEvent


class CodexMCPServer:
    def __init__(self, container):
        self.container = container
        self._tools = {
            "project_memory_resolve": self._resolve,
            "project_memory_ingest": self._ingest,
            "project_memory_recall": self._recall,
            "project_memory_feedback": self._feedback,
            "project_memory_end_session": self._end_session,
            "project_memory_status": self._status,
        }

    def list_tools(self) -> list[dict[str, str]]:
        return [{"name": name, "description": name.replace("_", " ")} for name in self._tools]

    def call_tool(self, name: str, args: dict):
        if name not in self._tools:
            raise KeyError(name)
        return self._tools[name](args)

    def _resolve(self, args: dict):
        project = ProjectContext.model_validate(args["project"])
        resolved = self.container.projects.ensure_project(project)
        return resolved.model_dump()

    def _ingest(self, args: dict):
        project = ProjectContext.model_validate(args["project"])
        event = SessionEvent.model_validate(args["event"])
        return self.container.archive.append_event(args["session_id"], event, project)

    def _recall(self, args: dict):
        project = ProjectContext.model_validate(args["project"])
        return self.container.retrieval.recall(
            MemoryRecallRequest(project=project, query=args["query"])
        ).model_dump()

    def _feedback(self, args: dict):
        return self.container.conflicts.apply_feedback(
            FeedbackRequest.model_validate(args)
        ).model_dump()

    def _end_session(self, args: dict):
        project = ProjectContext.model_validate(args["project"])
        return self.container.archive.end_session(args["session_id"], project)

    def _status(self, args: dict):
        return self.container.reporter.report().model_dump(mode="json")
