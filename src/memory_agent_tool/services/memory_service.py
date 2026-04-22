from __future__ import annotations

from typing import Any

from memory_agent_tool.config import AppSettings, TrustConfig
from memory_agent_tool.database import Database
from memory_agent_tool.logging import get_logger
from memory_agent_tool.models import (
    ConflictState,
    DurabilityLevel,
    IngestedMemory,
    MemoryState,
    PromotionState,
    RuleOverlapState,
)
from memory_agent_tool.rules import LoadedRules, RulesLoader
from memory_agent_tool.scoring import normalize_text
from memory_agent_tool.services.conflict_service import row_to_ingested_memory
from memory_agent_tool.services.utils import extract_fact_key, now_ts, summarize_text

logger = get_logger("memory_service")


class ProjectMemoryService:
    def __init__(self, db: Database, settings: AppSettings, conflicts, trust: TrustConfig | None = None):
        self.db = db
        self.settings = settings
        self.conflicts = conflicts
        self.trust = trust or settings.trust

    def classify_durability(self, content: str, memory_type: str) -> DurabilityLevel:
        text = normalize_text(content)
        if len(text) > 500 or "traceback" in text or "debug" in text or "stack trace" in text:
            return DurabilityLevel.TRANSIENT
        if memory_type in {"procedure", "workflow"}:
            return DurabilityLevel.SKILL_CANDIDATE
        durable_markers = (
            "project",
            "repository",
            "always",
            "use ",
            "run ",
            "avoid",
            "build",
            "test",
            "backend",
            "framework",
            "command",
            "convention",
            "procedure",
            "fixed",
        )
        if any(marker in text for marker in durable_markers):
            return DurabilityLevel.PROJECT_DURABLE
        return DurabilityLevel.SESSION_RELEVANT

    def _active_budget(self, project_key: str) -> int:
        row = self.db.fetchone(
            """
            SELECT COALESCE(SUM(LENGTH(content)), 0) AS total_length
            FROM memory_items
            WHERE project_key = ? AND state = ?
            """,
            (project_key, MemoryState.PINNED_ACTIVE.value),
        )
        return int(row["total_length"]) if row else 0

    def _consolidate_if_needed(self, project_key: str, extra_chars: int) -> None:
        current = self._active_budget(project_key)
        if current + extra_chars <= self.settings.pinned_memory_char_budget:
            return
        candidates = self.db.fetchall(
            """
            SELECT memory_id
            FROM memory_items
            WHERE project_key = ? AND state = ?
            ORDER BY trust_score ASC, updated_at ASC
            """,
            (project_key, MemoryState.PINNED_ACTIVE.value),
        )
        for row in candidates:
            self.db.execute(
                """
                UPDATE memory_items
                SET state = ?, conflict_state = ?, updated_at = ?
                WHERE memory_id = ?
                """,
                (MemoryState.DEGRADED.value, ConflictState.SUPERSEDED.value, now_ts(), row["memory_id"]),
            )
            current = self._active_budget(project_key)
            if current + extra_chars <= self.settings.pinned_memory_char_budget:
                return

    def list_active(self, project_key: str) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            """
            SELECT *
            FROM memory_items
            WHERE project_key = ?
              AND state = ?
              AND conflict_state IN (?, ?)
              AND rule_overlap_state = ?
            ORDER BY trust_score DESC, COALESCE(last_verified_at, updated_at) DESC, updated_at DESC
            """,
            (
                project_key,
                MemoryState.PINNED_ACTIVE.value,
                ConflictState.NONE.value,
                ConflictState.CONFIRMED.value,
                RuleOverlapState.NONE.value,
            ),
        )
        return [dict(row) for row in rows]

    def ingest(
        self,
        project_key: str,
        content: str,
        memory_type: str,
        title: str | None,
        loaded_rules: LoadedRules,
        source_kind: str,
        source_session_id: str | None = None,
        source_message_id: int | None = None,
        recalled_from_memory: bool = False,
    ) -> IngestedMemory:
        durability = self.classify_durability(content, memory_type)
        rule_overlap = RuleOverlapState(
            RulesLoader().detect_overlap(content, loaded_rules)
        )
        fact_key = extract_fact_key(title, content)
        duplicate = self.db.fetchone(
            """
            SELECT memory_id
            FROM memory_items
            WHERE project_key = ? AND LOWER(content) = LOWER(?)
              AND state IN (?, ?, ?)
            """,
            (
                project_key,
                content,
                MemoryState.SESSION_ONLY.value,
                MemoryState.PINNED_ACTIVE.value,
                MemoryState.CONFLICT_CANDIDATE.value,
            ),
        )
        details = "Stored as session-only."
        conflict_id: int | None = None
        if duplicate:
            existing = dict(self.db.fetchone("SELECT * FROM memory_items WHERE memory_id = ?", (duplicate["memory_id"],)))
            logger.info("duplicate rejected: project=%s fact_key=%s", project_key, fact_key)
            return row_to_ingested_memory(existing, "Duplicate content rejected.")

        state = MemoryState.SESSION_ONLY
        conflict_state = ConflictState.NONE
        trust_score = self.trust.initial_trust
        if recalled_from_memory:
            details = "Recall capture guard prevented pinned write."
        elif rule_overlap != RuleOverlapState.NONE:
            details = "Rule overlap detected. Kept out of pinned memory."
        elif durability in {DurabilityLevel.PROJECT_DURABLE, DurabilityLevel.SKILL_CANDIDATE}:
            has_conflict, conflict_id = self.conflicts.detect_conflict(project_key, fact_key, content)
            if has_conflict:
                state = MemoryState.CONFLICT_CANDIDATE
                conflict_state = ConflictState.SUSPECTED
                details = f"Conflicts with pinned memory #{conflict_id}."
            else:
                state = MemoryState.MEMORY_CANDIDATE
                details = "Memory candidate accepted."
        else:
            details = "Durability check kept content in session archive only."

        ts = now_ts()
        cursor = self.db.execute(
            """
            INSERT INTO memory_items(
                project_key, memory_type, title, summary, content, fact_key,
                source_kind, source_session_id, source_message_id, state,
                durability_level, trust_score, conflict_state, rule_overlap_state,
                recall_capture_guard, last_verified_at, promotion_state, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_key,
                memory_type,
                title,
                summarize_text(content),
                content,
                fact_key,
                source_kind,
                source_session_id,
                source_message_id,
                state.value,
                durability.value,
                trust_score,
                conflict_state.value,
                rule_overlap.value,
                1 if recalled_from_memory else 0,
                ts if state == MemoryState.PINNED_ACTIVE else None,
                PromotionState.CANDIDATE.value if durability == DurabilityLevel.SKILL_CANDIDATE else PromotionState.NONE.value,
                ts,
                ts,
            ),
        )
        memory_id = int(cursor.lastrowid)
        if state == MemoryState.CONFLICT_CANDIDATE and conflict_id is not None:
            self.conflicts.record_conflict(project_key, conflict_id, memory_id, details, "suspected")
            resolution = self.conflicts.resolve_conflict(project_key, conflict_id, memory_id)
            details = f"Conflict resolution: {resolution.resolution}."

        if state == MemoryState.MEMORY_CANDIDATE:
            self._consolidate_if_needed(project_key, len(content))
            self.db.execute(
                """
                UPDATE memory_items
                SET state = ?, last_verified_at = ?, updated_at = ?
                WHERE memory_id = ?
                """,
                (MemoryState.PINNED_ACTIVE.value, ts, ts, memory_id),
            )
            details = "Pinned memory activated."

        row = dict(self.db.fetchone("SELECT * FROM memory_items WHERE memory_id = ?", (memory_id,)))
        logger.info("memory ingested: project=%s memory_id=%d state=%s durability=%s", project_key, memory_id, state.value, durability.value)
        return row_to_ingested_memory(row, details)
