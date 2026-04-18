from __future__ import annotations

from pathlib import Path

from memory_agent_tool.models import MemoryRecallRequest, ProjectContext
from memory_agent_tool.resolver import ProjectResolver
from memory_agent_tool.services import AppContainer


def make_context(tmp_path: Path, **kwargs) -> ProjectContext:
    payload = {
        "repo_identity": str(tmp_path / "repo"),
        "workspace": "shared",
        "tool_name": "codex",
        "working_directory": str(tmp_path),
    }
    payload.update(kwargs)
    return ProjectContext(**payload)


def test_same_repo_different_branch_still_shares_default_project_key(tmp_path: Path):
    resolver = ProjectResolver()
    first = resolver.resolve(make_context(tmp_path, branch="main"))
    second = resolver.resolve(make_context(tmp_path, branch="feature/test"))
    assert first.project_scope_metadata["canonical_project_key"] == second.project_scope_metadata["canonical_project_key"]


def test_monorepo_subpath_can_isolate_finer_scope_but_keep_same_canonical_key(tmp_path: Path):
    resolver = ProjectResolver()
    first = resolver.resolve(make_context(tmp_path, working_directory=str(tmp_path / "packages" / "a"), monorepo_subpath="packages/a"))
    second = resolver.resolve(make_context(tmp_path, working_directory=str(tmp_path / "packages" / "b"), monorepo_subpath="packages/b"))
    assert first.project_key != second.project_key
    assert first.project_scope_metadata["canonical_project_key"] == second.project_scope_metadata["canonical_project_key"]


def test_old_project_key_alias_can_still_recall_data(container: AppContainer, tmp_path: Path):
    context = make_context(tmp_path)
    resolved = container.projects.ensure_project(context)
    loaded = container.rules_loader.load(context)
    memory = container.memory.ingest(
        project_key=resolved.project_key,
        content="API framework: FastAPI",
        memory_type="fact",
        title="api framework",
        loaded_rules=loaded,
        source_kind="direct",
    )
    old_key = "repo::shared"
    container.projects.register_alias(old_key, resolved.project_key)
    old_context = make_context(tmp_path)
    bundle = container.retrieval.recall(
        MemoryRecallRequest(project=old_context, query="api framework")
    )
    assert any("FastAPI" in item.summary for item in bundle.fixed_memory_summary)
    assert memory.memory_id > 0
