from __future__ import annotations

import json
from typing import Any

from memory_agent_tool.config import TrustConfig
from memory_agent_tool.database import Database
from memory_agent_tool.logging import get_logger
from memory_agent_tool.models import (
    ConflictState,
    ConsolidationResult,
    MemoryState,
    RebuildResult,
    StaleReviewResult,
)
from memory_agent_tool.services.memory_service import ProjectMemoryService
from memory_agent_tool.services.session_service import SessionArchiveService
from memory_agent_tool.services.utils import build_focused_summary, freshness_score, now_ts

logger = get_logger("maintenance_service")


class MemoryMaintenanceService:
    def __init__(self, db: Database, memory: ProjectMemoryService, archive: SessionArchiveService, trust: TrustConfig | None = None):
        self.db = db
        self.memory = memory
        self.archive = archive
        self.trust = trust or TrustConfig()

    def _record_run(self, project_key: str | None, action: str, result: dict[str, Any]) -> None:
        self.db.execute(
            """
            INSERT INTO maintenance_runs(project_key, action, result_json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (project_key, action, json.dumps(result, ensure_ascii=False), now_ts()),
        )

    def review_stale_memories(self, project_key: str) -> StaleReviewResult:
        rows = self.db.fetchall(
            """
            SELECT memory_id, trust_score, last_verified_at, updated_at, state
            FROM memory_items
            WHERE project_key = ? AND state = ?
            """,
            (project_key, MemoryState.PINNED_ACTIVE.value),
        )
        review_candidates = 0
        degraded = 0
        for row in rows:
            freshness = freshness_score(float(row["updated_at"] or 0), float(row["last_verified_at"] or 0))
            trust = float(row["trust_score"] or 0)
            if freshness <= self.trust.stale_freshness_threshold and trust < self.trust.stale_trust_threshold:
                review_candidates += 1
                new_state = MemoryState.DEGRADED.value if trust < self.trust.degrade_threshold else MemoryState.SESSION_ONLY.value
                self.db.execute(
                    """
                    UPDATE memory_items
                    SET state = ?, updated_at = ?
                    WHERE memory_id = ?
                    """,
                    (new_state, now_ts(), row["memory_id"]),
                )
                if new_state == MemoryState.DEGRADED.value:
                    degraded += 1
        result = StaleReviewResult(project_key=project_key, review_candidates=review_candidates, degraded=degraded)
        self._record_run(project_key, "review_stale_memories", result.model_dump())
        logger.info("stale review: project=%s candidates=%d degraded=%d", project_key, review_candidates, degraded)
        return result

    def consolidate_project_memory(self, project_key: str) -> ConsolidationResult:
        rows = self.db.fetchall(
            """
            SELECT *
            FROM memory_items
            WHERE project_key = ?
              AND state IN (?, ?)
            ORDER BY fact_key, updated_at DESC
            """,
            (project_key, MemoryState.PINNED_ACTIVE.value, MemoryState.CONFLICT_CANDIDATE.value),
        )
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(row["fact_key"] or row["summary"], []).append(dict(row))
        consolidated = 0
        for fact_key, group in grouped.items():
            if len(group) < 2:
                continue
            leader = group[0]
            followers = group[1:]
            merged_summary = "; ".join(dict.fromkeys(item["summary"] for item in group))
            self.db.execute(
                """
                UPDATE memory_items
                SET summary = ?, updated_at = ?, last_verified_at = ?
                WHERE memory_id = ?
                """,
                (merged_summary[:180], now_ts(), now_ts(), leader["memory_id"]),
            )
            for follower in followers:
                if leader["memory_id"] == follower["memory_id"]:
                    continue
                self.db.execute(
                    """
                    INSERT INTO memory_edges(project_key, from_memory_id, to_memory_id, relation_type, created_at)
                    VALUES (?, ?, ?, 'supersedes', ?)
                    """,
                    (project_key, leader["memory_id"], follower["memory_id"], now_ts()),
                )
                self.db.execute(
                    """
                    UPDATE memory_items
                    SET state = ?, conflict_state = ?, updated_at = ?
                    WHERE memory_id = ?
                    """,
                    (MemoryState.DEGRADED.value, ConflictState.SUPERSEDED.value, now_ts(), follower["memory_id"]),
                )
                consolidated += 1
        result = ConsolidationResult(project_key=project_key, consolidated=consolidated)
        self._record_run(project_key, "consolidate_project_memory", result.model_dump())
        logger.info("consolidation: project=%s consolidated=%d", project_key, consolidated)
        return result

    def rebuild_session_summaries(self, project_key: str | None = None) -> RebuildResult:
        params: tuple[Any, ...] = ()
        sql = "SELECT DISTINCT project_key, session_id FROM project_sessions"
        if project_key:
            sql += " WHERE project_key = ?"
            params = (project_key,)
        sessions = self.db.fetchall(sql, params)
        rebuilt = 0
        for session in sessions:
            rows = self.db.fetchall(
                """
                SELECT normalized_summary, content
                FROM project_messages
                WHERE session_id = ?
                ORDER BY created_at ASC
                """,
                (session["session_id"],),
            )
            summary = build_focused_summary([dict(row) for row in rows])
            existing = self.db.fetchone(
                "SELECT summary_id FROM session_summaries WHERE project_key = ? AND session_id = ?",
                (session["project_key"], session["session_id"]),
            )
            if existing is None:
                self.db.execute(
                    """
                    INSERT INTO session_summaries(project_key, session_id, summary, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (session["project_key"], session["session_id"], summary, now_ts()),
                )
            else:
                self.db.execute(
                    """
                    UPDATE session_summaries
                    SET summary = ?, updated_at = ?
                    WHERE summary_id = ?
                    """,
                    (summary, now_ts(), existing["summary_id"]),
                )
            rebuilt += 1
        result = RebuildResult(project_key=project_key, rebuilt=rebuilt)
        self._record_run(project_key, "rebuild_session_summaries", result.model_dump())
        logger.info("summaries rebuilt: project=%s rebuilt=%d", project_key, rebuilt)
        return result
