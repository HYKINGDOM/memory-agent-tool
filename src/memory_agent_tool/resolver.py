from __future__ import annotations

import hashlib
import re
from pathlib import Path

from memory_agent_tool.models import ProjectContext, ResolvedProject


def _slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"^[a-z]+://", "", value)
    value = value.replace("\\", "/")
    value = re.sub(r"[^a-z0-9/._-]+", "-", value)
    value = re.sub(r"/{2,}", "/", value)
    value = value.strip("-/")
    return value or "project"


def _normalize_repo_identity(repo_identity: str, working_directory: str | None) -> str:
    candidate = repo_identity or working_directory or "project"
    normalized = _slugify(candidate)
    if "/" not in normalized:
        return normalized
    parts = [part for part in normalized.split("/") if part and part not in {"users", "home", "private", "var", "folders"}]
    return "/".join(parts[-3:]) if parts else normalized


def _derive_repo_slug(normalized_repo_identity: str) -> str:
    repo_name = normalized_repo_identity.split("/")[-1]
    return _slugify(repo_name)


def _derive_monorepo_subpath(context: ProjectContext, repo_identity: str) -> str | None:
    if context.monorepo_subpath:
        return _slugify(context.monorepo_subpath)
    if not context.working_directory:
        return None
    try:
        repo_path = Path(repo_identity).resolve()
        working_path = Path(context.working_directory).resolve()
    except OSError:
        return None
    if not repo_path.exists() or not working_path.exists():
        return None
    try:
        relative = working_path.relative_to(repo_path)
    except ValueError:
        return None
    if not relative.parts:
        return None
    if len(relative.parts) <= 1:
        return None
    return _slugify("/".join(relative.parts))


class ProjectResolver:
    def resolve(self, context: ProjectContext) -> ResolvedProject:
        repo_identity = context.repo_identity or context.working_directory or "project"
        normalized_repo_identity = _normalize_repo_identity(repo_identity, context.working_directory)
        repo_slug = _derive_repo_slug(normalized_repo_identity)
        workspace_slug = _slugify(context.workspace) if context.workspace else ""
        namespace_slug = _slugify(context.namespace) if context.namespace else ""
        monorepo_subpath = _derive_monorepo_subpath(context, repo_identity)

        scope_components = [repo_slug]
        key_parts = [repo_slug]

        if namespace_slug:
            scope_components.append(namespace_slug)
            key_parts.append(namespace_slug)
        if workspace_slug:
            scope_components.append(workspace_slug)
            key_parts.append(workspace_slug)

        project_key = "::".join(key_parts)
        canonical_project_key = project_key

        branch_slug = _slugify(context.branch) if context.branch else ""
        if branch_slug or monorepo_subpath:
            fine_scope_seed = "::".join(
                part
                for part in [namespace_slug, workspace_slug, monorepo_subpath or "", branch_slug]
                if part
            )
            if fine_scope_seed:
                project_key = f"{project_key}::{hashlib.sha1(fine_scope_seed.encode('utf-8')).hexdigest()[:8]}"
                scope_components.extend(part for part in [monorepo_subpath, branch_slug] if part)

        metadata = {
            "canonical_project_key": canonical_project_key,
            "repo_identity": normalized_repo_identity,
            "repo_identity_raw": repo_identity,
            "workspace": context.workspace,
            "branch": context.branch,
            "namespace": context.namespace,
            "monorepo_subpath": monorepo_subpath,
            "tool_name": context.tool_name,
            "working_directory": context.working_directory,
            "scope_components": scope_components,
            "scope_filters": {
                "workspace": workspace_slug or None,
                "namespace": namespace_slug or None,
                "branch": branch_slug or None,
                "monorepo_subpath": monorepo_subpath,
            },
        }
        return ResolvedProject(project_key=project_key, project_scope_metadata=metadata)
