from __future__ import annotations

import json
from typing import Any

from memory_agent_tool.database import Database
from memory_agent_tool.logging import get_logger
from memory_agent_tool.models import AliasSummaryResult
from memory_agent_tool.resolver import ProjectResolver
from memory_agent_tool.services.utils import now_ts

logger = get_logger("project_service")


class ProjectRegistry:
    def __init__(self, db: Database, resolver: ProjectResolver):
        self.db = db
        self.resolver = resolver

    def ensure_project(self, request) -> Any:
        resolved = self.resolver.resolve(request)
        alias = self.db.fetchone(
            "SELECT canonical_project_key FROM project_aliases WHERE alias_key = ?",
            (resolved.project_key,),
        )
        if alias is not None:
            self._record_alias_usage(resolved.project_key, alias["canonical_project_key"])
            resolved = resolved.model_copy(update={"project_key": alias["canonical_project_key"]})
        ts = now_ts()
        display_name = resolved.project_key.replace("::", " / ")
        self.db.execute(
            """
            INSERT INTO projects(
                project_key, canonical_project_key, display_name, repo_identity, namespace, workspace, branch,
                monorepo_subpath, scope_components_json, status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
            ON CONFLICT(project_key) DO UPDATE SET
                canonical_project_key=excluded.canonical_project_key,
                display_name=excluded.display_name,
                repo_identity=excluded.repo_identity,
                namespace=excluded.namespace,
                workspace=excluded.workspace,
                branch=excluded.branch,
                monorepo_subpath=excluded.monorepo_subpath,
                scope_components_json=excluded.scope_components_json,
                updated_at=excluded.updated_at
            """,
            (
                resolved.project_key,
                resolved.project_scope_metadata.get("canonical_project_key", resolved.project_key),
                display_name,
                resolved.project_scope_metadata.get("repo_identity"),
                resolved.project_scope_metadata.get("namespace"),
                resolved.project_scope_metadata.get("workspace"),
                resolved.project_scope_metadata.get("branch"),
                resolved.project_scope_metadata.get("monorepo_subpath"),
                json.dumps(resolved.project_scope_metadata.get("scope_components", []), ensure_ascii=False),
                ts,
                ts,
            ),
        )
        logger.info("project ensured: %s", resolved.project_key)
        return resolved

    def register_alias(self, alias_key: str, canonical_project_key: str) -> None:
        self.db.execute(
            """
            INSERT INTO project_aliases(alias_key, canonical_project_key, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(alias_key) DO UPDATE SET
                canonical_project_key = excluded.canonical_project_key,
                created_at = excluded.created_at
            """,
            (alias_key, canonical_project_key, now_ts()),
        )
        logger.info("alias registered: %s -> %s", alias_key, canonical_project_key)

    def _record_alias_usage(self, alias_key: str, canonical_project_key: str) -> None:
        self.db.execute(
            """
            INSERT INTO service_state(state_key, state_value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(state_key) DO UPDATE SET
                state_value = excluded.state_value,
                updated_at = excluded.updated_at
            """,
            (
                f"project_alias_usage:{alias_key}",
                json.dumps(
                    {
                        "alias_key": alias_key,
                        "canonical_project_key": canonical_project_key,
                        "used_at": now_ts(),
                    },
                    ensure_ascii=False,
                ),
                now_ts(),
            ),
        )

    def alias_summary(self) -> AliasSummaryResult:
        count_row = self.db.fetchone("SELECT COUNT(*) AS count FROM project_aliases")
        usage_rows = self.db.fetchall(
            """
            SELECT state_value
            FROM service_state
            WHERE state_key LIKE 'project_alias_usage:%'
            ORDER BY updated_at DESC
            LIMIT 5
            """
        )
        latest_usage = [json.loads(row["state_value"]) for row in usage_rows]
        alias_rows = self.db.fetchall(
            """
            SELECT alias_key, canonical_project_key, created_at
            FROM project_aliases
            ORDER BY created_at DESC
            LIMIT 10
            """
        )
        return AliasSummaryResult(
            alias_count=int(count_row["count"]) if count_row else 0,
            recent_aliases=[dict(row) for row in alias_rows],
            recent_alias_usage=latest_usage,
        )
