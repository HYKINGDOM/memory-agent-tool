from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock

import pytest

from memory_agent_tool.client_acceptance import (
    ClientAcceptanceTester,
    ClientTestResult,
    JSONFormatter,
    MarkdownFormatter,
    ReportFormatter,
    ReportPayloadBuilder,
    build_client_context,
)
from memory_agent_tool.models import ProjectContext
from memory_agent_tool.services import AppContainer


def _make_mock_container() -> MagicMock:
    container = MagicMock(spec=AppContainer)
    container.settings.root_dir = Path("/tmp/test-project")
    return container


def _make_copilot_adapter() -> MagicMock:
    adapter = MagicMock()
    adapter.mount_project_memory_server.return_value = {"status": "mounted", "reused": False}
    adapter.handshake.return_value = {
        "agent_name": "copilot-agent",
        "session_id": "copilot-session-001",
    }
    return adapter


def _make_trae_adapter(chat_status: str = "chat_opened") -> MagicMock:
    adapter = MagicMock()
    adapter.open_chat_session.return_value = {
        "status": chat_status,
        "mount": {"status": "mounted", "reused": False},
        "prompt": "Use the project memory MCP server for this workspace.",
    }
    return adapter


class TestBuildClientContext:
    def test_returns_project_context_with_tool_name(self):
        container = _make_mock_container()
        context = build_client_context(container, "copilot")
        assert isinstance(context, ProjectContext)
        assert context.tool_name == "copilot"
        assert context.client_type == "copilot_cli"
        assert context.client_session_id == "copilot-cli-session"

    def test_different_tool_names_produce_different_contexts(self):
        container = _make_mock_container()
        copilot_ctx = build_client_context(container, "copilot")
        trae_ctx = build_client_context(container, "trae")
        assert copilot_ctx.tool_name != trae_ctx.tool_name
        assert copilot_ctx.client_type != trae_ctx.client_type


class TestClientTestResult:
    def test_dataclass_fields(self):
        result = ClientTestResult(client_name="copilot", status="passed", data={"key": "value"})
        assert result.client_name == "copilot"
        assert result.status == "passed"
        assert result.data == {"key": "value"}

    def test_default_data_is_empty_dict(self):
        result = ClientTestResult(client_name="trae", status="mounted")
        assert result.data == {}


class TestClientAcceptanceTester:
    def test_test_copilot_returns_passed_status(self):
        container = _make_mock_container()
        copilot_adapter = _make_copilot_adapter()
        container.client_registry.get.return_value = copilot_adapter

        tester = ClientAcceptanceTester(container)
        result = tester.test_client("copilot")

        assert result.client_name == "copilot"
        assert result.status == "passed"
        assert "mount" in result.data
        assert "handshake" in result.data
        copilot_adapter.mount_project_memory_server.assert_called_once()
        copilot_adapter.handshake.assert_called_once()

    def test_test_trae_returns_passed_when_chat_opened(self):
        container = _make_mock_container()
        trae_adapter = _make_trae_adapter(chat_status="chat_opened")
        container.client_registry.get.return_value = trae_adapter

        tester = ClientAcceptanceTester(container)
        result = tester.test_client("trae")

        assert result.client_name == "trae"
        assert result.status == "passed"
        assert "chat" in result.data
        assert "mount" in result.data
        trae_adapter.open_chat_session.assert_called_once()

    def test_test_trae_returns_mounted_when_chat_not_opened(self):
        container = _make_mock_container()
        trae_adapter = _make_trae_adapter(chat_status="mounting")
        container.client_registry.get.return_value = trae_adapter

        tester = ClientAcceptanceTester(container)
        result = tester.test_client("trae")

        assert result.status == "mounted"

    def test_test_client_raises_for_unknown_client(self):
        container = _make_mock_container()
        tester = ClientAcceptanceTester(container)
        with pytest.raises(ValueError, match="Unknown client: vscode"):
            tester.test_client("vscode")

    def test_run_all_tests_returns_both_clients(self):
        container = _make_mock_container()

        def get_adapter(name: str) -> MagicMock:
            if name == "copilot_real":
                return _make_copilot_adapter()
            if name == "trae_real":
                return _make_trae_adapter()
            raise KeyError(name)

        container.client_registry.get = get_adapter

        tester = ClientAcceptanceTester(container)
        results = tester.run_all_tests()

        assert "copilot" in results
        assert "trae" in results
        assert results["copilot"].status == "passed"
        assert results["trae"].status == "passed"

    def test_trae_mount_defaults_when_missing_in_chat(self):
        container = _make_mock_container()
        trae_adapter = MagicMock()
        trae_adapter.open_chat_session.return_value = {"status": "chat_opened"}
        container.client_registry.get.return_value = trae_adapter

        tester = ClientAcceptanceTester(container)
        result = tester.test_client("trae")

        assert result.data["mount"] == {"status": "mounted", "reused": False}


class TestReportPayloadBuilder:
    def test_build_produces_expected_structure(self):
        test_results = {
            "copilot": ClientTestResult(
                client_name="copilot",
                status="passed",
                data={"mount": {"status": "mounted"}, "handshake": {"agent_name": "a"}},
            ),
            "trae": ClientTestResult(
                client_name="trae",
                status="passed",
                data={"chat": {"status": "chat_opened"}, "mount": {"status": "mounted"}},
            ),
        }
        payload = ReportPayloadBuilder.build(test_results)

        assert payload["format"] == "json"
        assert payload["generated_by"] == "memory-agent-tool"
        assert "copilot" in payload["clients"]
        assert "trae" in payload["clients"]
        assert payload["clients"]["copilot"]["status"] == "passed"
        assert payload["clients"]["trae"]["status"] == "passed"

    def test_build_merges_status_with_data(self):
        test_results = {
            "copilot": ClientTestResult(
                client_name="copilot",
                status="passed",
                data={"mount": {"status": "mounted"}},
            ),
        }
        payload = ReportPayloadBuilder.build(test_results)

        copilot_data = payload["clients"]["copilot"]
        assert copilot_data["status"] == "passed"
        assert copilot_data["mount"] == {"status": "mounted"}

    def test_build_with_empty_results(self):
        payload = ReportPayloadBuilder.build({})
        assert payload["clients"] == {}


class TestJSONFormatter:
    def test_format_returns_valid_json(self):
        payload = {"format": "json", "clients": {"copilot": {"status": "passed"}}}
        result = JSONFormatter().format(payload)
        parsed = json.loads(result)
        assert parsed == payload

    def test_format_preserves_unicode(self):
        payload = {"clients": {"copilot": {"status": "通过"}}}
        result = JSONFormatter().format(payload)
        assert "通过" in result
        assert "\\u" not in result


class TestMarkdownFormatter:
    def test_format_produces_header(self):
        payload = {
            "clients": {
                "copilot": {
                    "status": "passed",
                    "handshake": {"agent_name": "copilot-agent", "session_id": "s1"},
                },
            }
        }
        result = MarkdownFormatter().format(payload)
        assert result.startswith("# Real Client Acceptance Report")
        assert "## Copilot" in result
        assert "- Status: passed" in result

    def test_format_copilot_fields(self):
        payload = {
            "clients": {
                "copilot": {
                    "status": "passed",
                    "handshake": {"agent_name": "my-agent", "session_id": "sess-123"},
                },
            }
        }
        result = MarkdownFormatter().format(payload)
        assert "- Agent: my-agent" in result
        assert "- Session: sess-123" in result

    def test_format_trae_fields(self):
        payload = {
            "clients": {
                "trae": {
                    "status": "passed",
                    "mount": {"status": "mounted"},
                    "chat": {"status": "chat_opened"},
                },
            }
        }
        result = MarkdownFormatter().format(payload)
        assert "## Trae" in result
        assert "- Mount: mounted" in result
        assert "- Chat: chat_opened" in result

    def test_format_multiple_clients(self):
        payload = {
            "clients": {
                "copilot": {
                    "status": "passed",
                    "handshake": {"agent_name": "a", "session_id": "s1"},
                },
                "trae": {
                    "status": "mounted",
                    "mount": {"status": "mounted"},
                    "chat": {"status": "mounting"},
                },
            }
        }
        result = MarkdownFormatter().format(payload)
        assert "## Copilot" in result
        assert "## Trae" in result


class TestReportFormatter:
    def test_format_json_by_default(self):
        payload = {"format": "json", "clients": {}}
        formatter = ReportFormatter()
        result = formatter.format(payload)
        assert json.loads(result) == payload

    def test_format_markdown(self):
        payload = {
            "clients": {
                "copilot": {
                    "status": "passed",
                    "handshake": {"agent_name": "a", "session_id": "s1"},
                },
            }
        }
        formatter = ReportFormatter()
        result = formatter.format(payload, "markdown")
        assert result.startswith("# Real Client Acceptance Report")

    def test_format_raises_for_unsupported_format(self):
        payload = {"clients": {}}
        formatter = ReportFormatter()
        with pytest.raises(ValueError, match="Unsupported format: xml"):
            formatter.format(payload, "xml")
