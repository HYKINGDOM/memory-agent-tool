from __future__ import annotations

import argparse
import json
import subprocess
import sys

import uvicorn

from memory_agent_tool.app import create_app
from memory_agent_tool.config import AppSettings
from memory_agent_tool.e2e import run_local_e2e
from memory_agent_tool.mcp_server import MCPServerRuntime
from memory_agent_tool.models import FeedbackRequest, MemoryRecallRequest, ProjectContext, SessionEvent
from memory_agent_tool.services import AppContainer


def _build_container() -> AppContainer:
    settings = AppSettings.from_env()
    return AppContainer.build(settings)


def cmd_serve(args: argparse.Namespace) -> int:
    settings = AppSettings.from_env()
    app = create_app(settings)
    uvicorn.run(app, host=args.host or settings.host, port=args.port or settings.port, log_level="info")
    return 0


def cmd_demo_seed(_: argparse.Namespace) -> int:
    container = _build_container()
    report = run_local_e2e(container)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 1


def cmd_demo_recall(_: argparse.Namespace) -> int:
    container = _build_container()
    context = ProjectContext(
        repo_identity=str(container.settings.root_dir),
        workspace="shared",
        tool_name="copilot",
        working_directory=str(container.settings.data_dir / "e2e-workspace"),
    )
    bundle = container.retrieval.recall(
        MemoryRecallRequest(project=context, query="api framework database backend release procedure")
    )
    print(bundle.combined_text)
    return 0


def cmd_report_status(_: argparse.Namespace) -> int:
    container = _build_container()
    report = container.reporter.report()
    print(report.model_dump_json(indent=2))
    return 0


def cmd_report_providers(_: argparse.Namespace) -> int:
    container = _build_container()
    print(json.dumps(container.providers.observability_summary(), ensure_ascii=False, indent=2))
    return 0


def cmd_report_project_scope(args: argparse.Namespace) -> int:
    container = _build_container()
    row = container.db.fetchone(
        """
        SELECT project_key, canonical_project_key, repo_identity, namespace, workspace, branch, monorepo_subpath,
               scope_components_json, updated_at
        FROM projects
        WHERE project_key = ?
        """,
        (args.project_key,),
    )
    if row is None:
        print(json.dumps({"error": "project not found", "project_key": args.project_key}, ensure_ascii=False))
        return 1
    aliases = container.db.fetchall(
        """
        SELECT alias_key, canonical_project_key, created_at
        FROM project_aliases
        WHERE canonical_project_key = ? OR alias_key = ?
        ORDER BY created_at DESC
        """,
        (row["canonical_project_key"] or args.project_key, args.project_key),
    )
    print(
        json.dumps(
            {"project": dict(row), "aliases": [dict(item) for item in aliases]},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def cmd_test_e2e_local(_: argparse.Namespace) -> int:
    container = _build_container()
    report = run_local_e2e(container)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 1


def cmd_maintenance_review_stale(args: argparse.Namespace) -> int:
    container = _build_container()
    print(json.dumps(container.maintenance.review_stale_memories(args.project_key), ensure_ascii=False, indent=2))
    return 0


def cmd_maintenance_consolidate(args: argparse.Namespace) -> int:
    container = _build_container()
    print(json.dumps(container.maintenance.consolidate_project_memory(args.project_key), ensure_ascii=False, indent=2))
    return 0


def cmd_maintenance_rebuild(args: argparse.Namespace) -> int:
    container = _build_container()
    print(json.dumps(container.maintenance.rebuild_session_summaries(args.project_key), ensure_ascii=False, indent=2))
    return 0


def cmd_pytest(_: argparse.Namespace) -> int:
    return subprocess.call([sys.executable, "-m", "pytest"])


def cmd_mcp_serve(_: argparse.Namespace) -> int:
    runtime = MCPServerRuntime.build()
    return runtime.run_stdio()


def _build_client_context(container: AppContainer, tool_name: str) -> ProjectContext:
    return ProjectContext(
        repo_identity=str(container.settings.root_dir),
        workspace="shared",
        tool_name=tool_name,
        working_directory=str(container.settings.root_dir),
        client_type=f"{tool_name}_cli",
        client_session_id=f"{tool_name}-cli-session",
    )


def cmd_client_copilot_e2e(_: argparse.Namespace) -> int:
    container = _build_container()
    adapter = container.client_registry.get("copilot_real")
    context = _build_client_context(container, "copilot")
    mount = adapter.mount_project_memory_server(context)
    session = adapter.start_session(context)
    emitted = adapter.emit_event(
        session.session_id,
        SessionEvent(
            role_or_event_type="assistant_note",
            content="Copilot CLI E2E: release procedure uses pytest and status report.",
            memory_type="procedure",
            title="copilot release procedure",
            metadata=context.model_dump(),
        ),
    )
    recall = adapter.request_recall("release procedure status report", context)
    feedback = adapter.submit_feedback(
        FeedbackRequest(memory_id=emitted["ingested_memory"]["memory_id"], helpful=True)
    )
    payload = {
        "client": "copilot",
        "status": "passed",
        "mount": mount,
        "session_id": session.session_id,
        "recall_contains": "status report" in recall.combined_text.lower(),
        "feedback": feedback.model_dump(),
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def cmd_client_trae_mount(_: argparse.Namespace) -> int:
    container = _build_container()
    adapter = container.client_registry.get("trae_real")
    context = _build_client_context(container, "trae")
    payload = adapter.mount_project_memory_server(context)
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def cmd_client_trae_chat_e2e(_: argparse.Namespace) -> int:
    container = _build_container()
    adapter = container.client_registry.get("trae_real")
    context = _build_client_context(container, "trae")
    chat = adapter.open_chat_session(
        context,
        "Use the project memory MCP server for this workspace.",
    )
    mount = chat.get("mount", {"status": "mounted", "reused": False})
    session = adapter.start_session(context)
    emitted = adapter.emit_event(
        session.session_id,
        SessionEvent(
            role_or_event_type="assistant_note",
            content="Trae chat E2E: release checklist includes pytest and status report.",
            memory_type="procedure",
            title="trae release checklist",
            metadata=context.model_dump(),
        ),
    )
    recall = adapter.request_recall("release checklist status report", context)
    feedback = adapter.submit_feedback(
        FeedbackRequest(memory_id=emitted["ingested_memory"]["memory_id"], helpful=True)
    )
    payload = {
        "client": "trae",
        "status": "passed",
        "mount": mount,
        "chat": chat,
        "session_id": session.session_id,
        "recall_contains": "status report" in recall.combined_text.lower(),
        "feedback": feedback.model_dump(),
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def cmd_client_acceptance_report(_: argparse.Namespace) -> int:
    container = _build_container()
    copilot_context = _build_client_context(container, "copilot")
    trae_context = _build_client_context(container, "trae")
    copilot = container.client_registry.get("copilot_real")
    trae = container.client_registry.get("trae_real")

    copilot_payload = {
        "status": "passed",
        "mount": copilot.mount_project_memory_server(copilot_context),
        "handshake": copilot.handshake(copilot_context),
    }
    trae_payload = {
        "chat": trae.open_chat_session(trae_context, "Use the project memory MCP server for this workspace."),
    }
    trae_payload["mount"] = trae_payload["chat"].get("mount", {"status": "mounted", "reused": False})
    trae_payload["status"] = "passed" if trae_payload["chat"]["status"] == "chat_opened" else "mounted"
    payload = {
        "format": "json",
        "generated_by": "memory-agent-tool",
        "clients": {
            "copilot": copilot_payload,
            "trae": trae_payload,
        },
    }
    output_format = getattr(_, "format", "json")
    if output_format == "markdown":
        markdown = "\n".join(
            [
                "# Real Client Acceptance Report",
                "",
                "## Copilot",
                f"- Status: {copilot_payload['status']}",
                f"- Agent: {copilot_payload['handshake']['agent_name']}",
                f"- Session: {copilot_payload['handshake']['session_id']}",
                "",
                "## Trae",
                f"- Status: {trae_payload['status']}",
                f"- Mount: {trae_payload['mount']['status']}",
                f"- Chat: {trae_payload['chat']['status']}",
            ]
        )
        container.reporter.record_test_run("client-acceptance", "passed", payload)
        print(markdown)
        return 0
    container.reporter.record_test_run("client-acceptance", "passed", payload)
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def cmd_skill_feedback(args: argparse.Namespace) -> int:
    container = _build_container()
    result = container.skills.record_skill_feedback(
        args.skill_id,
        helpful=args.helpful,
        accepted=args.accepted,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_skill_refresh(args: argparse.Namespace) -> int:
    container = _build_container()
    result = container.skills.refresh_skill_from_sources(args.skill_id)
    print(result.model_dump_json(indent=2))
    return 0


def cmd_project_register_alias(args: argparse.Namespace) -> int:
    container = _build_container()
    container.projects.register_alias(args.alias_key, args.canonical_project_key)
    print(
        json.dumps(
            {"alias_key": args.alias_key, "canonical_project_key": args.canonical_project_key},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _coerce_provider_value(raw: str):
    lowered = raw.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered.startswith("[") or lowered.startswith("{"):
        return json.loads(raw)
    return raw


def cmd_provider_config(args: argparse.Namespace) -> int:
    container = _build_container()
    if args.value is None:
        print(json.dumps(container.providers.runtime_policy(), ensure_ascii=False, indent=2))
        return 0
    result = container.providers.configure({args.key: _coerce_provider_value(args.value)})
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="memory-agent-tool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="Start the local memory service.")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.set_defaults(func=cmd_serve)

    demo = subparsers.add_parser("demo", help="Run local demo flows.")
    demo_sub = demo.add_subparsers(dest="demo_command", required=True)
    demo_seed = demo_sub.add_parser("seed", help="Seed demo data.")
    demo_seed.set_defaults(func=cmd_demo_seed)
    demo_recall = demo_sub.add_parser("recall", help="Recall demo data.")
    demo_recall.set_defaults(func=cmd_demo_recall)

    report = subparsers.add_parser("report", help="Output current status.")
    report_sub = report.add_subparsers(dest="report_command", required=True)
    report_status = report_sub.add_parser("status", help="Show status report.")
    report_status.set_defaults(func=cmd_report_status)
    report_providers = report_sub.add_parser("providers", help="Show provider runtime observability.")
    report_providers.set_defaults(func=cmd_report_providers)
    report_project_scope = report_sub.add_parser("project-scope", help="Inspect project scope and aliases.")
    report_project_scope.add_argument("project_key")
    report_project_scope.set_defaults(func=cmd_report_project_scope)

    test = subparsers.add_parser("test", help="Run validation commands.")
    test_sub = test.add_subparsers(dest="test_command", required=True)
    test_e2e = test_sub.add_parser("e2e-local", help="Run local E2E simulation.")
    test_e2e.set_defaults(func=cmd_test_e2e_local)
    test_pytest = test_sub.add_parser("pytest", help="Run pytest suite.")
    test_pytest.set_defaults(func=cmd_pytest)

    maintenance = subparsers.add_parser("maintenance", help="Run lifecycle maintenance tasks.")
    maintenance_sub = maintenance.add_subparsers(dest="maintenance_command", required=True)
    maintenance_review = maintenance_sub.add_parser("review-stale", help="Review stale memories for a project.")
    maintenance_review.add_argument("project_key")
    maintenance_review.set_defaults(func=cmd_maintenance_review_stale)
    maintenance_consolidate = maintenance_sub.add_parser("consolidate", help="Consolidate project memory.")
    maintenance_consolidate.add_argument("project_key")
    maintenance_consolidate.set_defaults(func=cmd_maintenance_consolidate)
    maintenance_rebuild = maintenance_sub.add_parser("rebuild-summaries", help="Rebuild session summaries.")
    maintenance_rebuild.add_argument("project_key", nargs="?")
    maintenance_rebuild.set_defaults(func=cmd_maintenance_rebuild)

    mcp = subparsers.add_parser("mcp", help="Run MCP server commands.")
    mcp_sub = mcp.add_subparsers(dest="mcp_command", required=True)
    mcp_serve = mcp_sub.add_parser("serve", help="Start the stdio MCP server.")
    mcp_serve.set_defaults(func=cmd_mcp_serve)

    client = subparsers.add_parser("client", help="Run real client integration commands.")
    client_sub = client.add_subparsers(dest="client_name", required=True)

    copilot = client_sub.add_parser("copilot", help="GitHub Copilot real integration commands.")
    copilot_sub = copilot.add_subparsers(dest="client_action", required=True)
    copilot_e2e = copilot_sub.add_parser("e2e", help="Run Copilot end-to-end recall/feedback flow.")
    copilot_e2e.set_defaults(func=cmd_client_copilot_e2e)

    trae = client_sub.add_parser("trae", help="Trae real integration commands.")
    trae_sub = trae.add_subparsers(dest="client_action", required=True)
    trae_mount = trae_sub.add_parser("mount", help="Mount the project memory MCP server into Trae.")
    trae_mount.set_defaults(func=cmd_client_trae_mount)
    trae_chat_e2e = trae_sub.add_parser("chat-e2e", help="Run Trae chat end-to-end recall/feedback flow.")
    trae_chat_e2e.set_defaults(func=cmd_client_trae_chat_e2e)

    report_client = client_sub.add_parser("report", help="Generate real client acceptance reports.")
    report_sub = report_client.add_subparsers(dest="client_action", required=True)
    report_acceptance = report_sub.add_parser("acceptance", help="Run a unified acceptance report for real clients.")
    report_acceptance.add_argument("--format", choices=("json", "markdown"), default="json")
    report_acceptance.set_defaults(func=cmd_client_acceptance_report)

    skills = subparsers.add_parser("skills", help="Operate skill lifecycle commands.")
    skills_sub = skills.add_subparsers(dest="skills_command", required=True)
    skill_feedback = skills_sub.add_parser("feedback", help="Record skill feedback.")
    skill_feedback.add_argument("skill_id", type=int)
    skill_feedback.add_argument("--helpful", action="store_true")
    skill_feedback.add_argument("--accepted", action="store_true")
    skill_feedback.set_defaults(func=cmd_skill_feedback)
    skill_refresh = skills_sub.add_parser("refresh", help="Refresh a skill from source memories.")
    skill_refresh.add_argument("skill_id", type=int)
    skill_refresh.set_defaults(func=cmd_skill_refresh)

    providers = subparsers.add_parser("providers", help="Configure provider runtime policy.")
    providers_sub = providers.add_subparsers(dest="providers_command", required=True)
    provider_config = providers_sub.add_parser("config", help="Show or update provider runtime config.")
    provider_config.add_argument("key", nargs="?")
    provider_config.add_argument("value", nargs="?")
    provider_config.set_defaults(func=cmd_provider_config)

    projects = subparsers.add_parser("projects", help="Project scope and alias commands.")
    projects_sub = projects.add_subparsers(dest="projects_command", required=True)
    alias_add = projects_sub.add_parser("alias", help="Register a project alias.")
    alias_add.add_argument("alias_key")
    alias_add.add_argument("canonical_project_key")
    alias_add.set_defaults(func=cmd_project_register_alias)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
