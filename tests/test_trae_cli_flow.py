from __future__ import annotations

import argparse
from types import SimpleNamespace

from memory_agent_tool import cli
from memory_agent_tool.models import FeedbackRequest


class _FakeTraeAdapter:
    def __init__(self) -> None:
        self.mount_calls = 0
        self.chat_calls = 0
        self.started_sessions: list[str] = []

    def mount_project_memory_server(self, context):
        self.mount_calls += 1
        return {"client": "trae", "status": "mounted", "reused": False}

    def open_chat_session(self, context, prompt: str):
        self.chat_calls += 1
        return {
            "client": "trae",
            "status": "chat_opened",
            "mount": {
                "client": "trae",
                "status": "mounted",
                "reused": False,
            },
        }

    def start_session(self, context):
        session_id = f"session-{len(self.started_sessions) + 1}"
        self.started_sessions.append(session_id)
        return SimpleNamespace(session_id=session_id)

    def emit_event(self, session_id: str, event):
        return {"ingested_memory": {"memory_id": 7}}

    def request_recall(self, query: str, context):
        return SimpleNamespace(combined_text="status report")

    def submit_feedback(self, request: FeedbackRequest):
        return SimpleNamespace(model_dump=lambda: {"trust_score": 0.75})


class _FakeCopilotAdapter:
    def mount_project_memory_server(self, context):
        return {"session_id": "copilot-session", "mcp_home": "/tmp/home"}

    def handshake(self, context):
        return {"agent_name": "Copilot", "session_id": "copilot-session", "models": {}}


class _FakeRegistry:
    def __init__(self, trae_adapter: _FakeTraeAdapter, copilot_adapter: _FakeCopilotAdapter):
        self._trae_adapter = trae_adapter
        self._copilot_adapter = copilot_adapter

    def get(self, name: str):
        if name == "trae_real":
            return self._trae_adapter
        if name == "copilot_real":
            return self._copilot_adapter
        raise KeyError(name)


class _FakeReporter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    def record_test_run(self, run_type: str, status: str, payload: dict) -> None:
        self.calls.append((run_type, status, payload))


class _FakeContainer:
    def __init__(self) -> None:
        self.settings = SimpleNamespace(root_dir="/tmp/project")
        self.trae_adapter = _FakeTraeAdapter()
        self.copilot_adapter = _FakeCopilotAdapter()
        self.client_registry = _FakeRegistry(self.trae_adapter, self.copilot_adapter)
        self.reporter = _FakeReporter()


def test_cli_trae_chat_e2e_skips_separate_mount(monkeypatch):
    container = _FakeContainer()
    monkeypatch.setattr(cli, "_build_container", lambda: container)

    exit_code = cli.cmd_client_trae_chat_e2e(argparse.Namespace())

    assert exit_code == 0
    assert container.trae_adapter.mount_calls == 0
    assert container.trae_adapter.chat_calls == 1


def test_cli_client_acceptance_report_skips_separate_trae_mount(monkeypatch):
    container = _FakeContainer()
    monkeypatch.setattr(cli, "_build_container", lambda: container)

    exit_code = cli.cmd_client_acceptance_report(argparse.Namespace(format="json"))

    assert exit_code == 0
    assert container.trae_adapter.mount_calls == 0
    assert container.trae_adapter.chat_calls == 1
    assert container.reporter.calls[0][0] == "client-acceptance"
