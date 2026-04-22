from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from memory_agent_tool.database import Database
from memory_agent_tool.logging import get_logger
from memory_agent_tool.models import (
    ConflictState,
    HealthResponse,
    MemoryState,
    StatusReport,
)
from memory_agent_tool.providers import ProviderManager
from memory_agent_tool.services.project_service import ProjectRegistry
from memory_agent_tool.services.skill_service import SkillPromotionService
from memory_agent_tool.services.utils import now_ts

logger = get_logger("status_service")


class StatusReporter:
    def __init__(self, db: Database, providers: ProviderManager, skills: SkillPromotionService, projects: ProjectRegistry):
        self.db = db
        self.providers = providers
        self.skills = skills
        self.projects = projects

    def health(self) -> HealthResponse:
        return HealthResponse(
            status="ok",
            database_writable=self.db.writable(),
            schema_version=self.db.schema_version(),
        )

    def report(self) -> StatusReport:
        stats = {}
        for table in (
            "projects",
            "project_sessions",
            "project_messages",
            "memory_items",
            "project_skills",
            "client_connections",
            "provider_runs",
            "provider_events",
            "memory_conflicts",
            "session_summaries",
        ):
            row = self.db.fetchone(f"SELECT COUNT(*) AS count FROM {table}")
            stats[table] = int(row["count"])
        recall_rows = self.db.fetchall(
            """
            SELECT project_key, query, used_sessions, created_at
            FROM retrieval_logs
            ORDER BY created_at DESC
            LIMIT 5
            """
        )
        conflict_rows = self.db.fetchall(
            """
            SELECT memory_id, summary, conflict_state, updated_at
            FROM memory_items
            WHERE conflict_state != ?
            ORDER BY updated_at DESC
            LIMIT 5
            """,
            (ConflictState.NONE.value,),
        )
        degraded_rows = self.db.fetchall(
            """
            SELECT memory_id, summary, trust_score, updated_at
            FROM memory_items
            WHERE state = ?
            ORDER BY updated_at DESC
            LIMIT 5
            """,
            (MemoryState.DEGRADED.value,),
        )
        latest_e2e = self.db.fetchone(
            """
            SELECT report_json
            FROM test_runs
            WHERE run_type = 'e2e-local'
            ORDER BY created_at DESC
            LIMIT 1
            """
        )
        latest_client_acceptance = self.db.fetchone(
            """
            SELECT report_json
            FROM test_runs
            WHERE run_type = 'client-acceptance'
            ORDER BY created_at DESC
            LIMIT 1
            """
        )
        stale_row = self.db.fetchone(
            """
            SELECT COUNT(*) AS count
            FROM memory_items
            WHERE state = ?
              AND COALESCE(last_verified_at, updated_at) < ?
            """,
            (MemoryState.PINNED_ACTIVE.value, now_ts() - (30 * 86400)),
        )
        review_row = self.db.fetchone(
            """
            SELECT COUNT(*) AS count
            FROM memory_items
            WHERE state IN (?, ?)
            """,
            (MemoryState.DEGRADED.value, MemoryState.SESSION_ONLY.value),
        )
        consolidated_row = self.db.fetchone(
            """
            SELECT COALESCE(SUM(CAST(json_extract(result_json, '$.consolidated') AS INTEGER)), 0) AS count
            FROM maintenance_runs
            WHERE action = 'consolidate_project_memory'
              AND created_at >= ?
            """,
            (now_ts() - (7 * 86400),),
        )
        provider_observability = self.providers.observability_summary()
        skill_observability = self.skills.observability_summary().model_dump()
        project_scope_observability = self.projects.alias_summary().model_dump()
        logger.info("status report generated: %d tables, %d recall hits", len(stats), len(recall_rows))
        return StatusReport(
            service_health="ok",
            schema_version=self.db.schema_version(),
            generated_at=datetime.now(timezone.utc),
            stats=stats,
            recent_recall_hits=[dict(row) for row in recall_rows],
            recent_conflicts=[dict(row) for row in conflict_rows],
            recent_degraded=[dict(row) for row in degraded_rows],
            stale_memory_count=int(stale_row["count"]) if stale_row else 0,
            review_candidate_count=int(review_row["count"]) if review_row else 0,
            recent_consolidated_count=int(consolidated_row["count"]) if consolidated_row else 0,
            provider_observability=provider_observability,
            skill_observability=skill_observability,
            project_scope_observability=project_scope_observability,
            recent_e2e_result=json.loads(latest_e2e["report_json"]) if latest_e2e else None,
            recent_client_acceptance_result=(
                json.loads(latest_client_acceptance["report_json"]) if latest_client_acceptance else None
            ),
        )

    def record_test_run(self, run_type: str, status: str, report: dict[str, Any]) -> None:
        self.db.execute(
            """
            INSERT INTO test_runs(run_type, status, report_json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (run_type, status, json.dumps(report, ensure_ascii=False), now_ts()),
        )
        logger.info("test run recorded: type=%s status=%s", run_type, status)
