from __future__ import annotations

from typing import Any

from memory_agent_tool.config import TrustConfig
from memory_agent_tool.database import Database
from memory_agent_tool.logging import get_logger
from memory_agent_tool.models import (
    ConflictResolutionResult,
    ConflictState,
    FeedbackRequest,
    IngestedMemory,
    MemoryState,
    PromotionState,
)
from memory_agent_tool.providers import ProviderManager
from memory_agent_tool.services.utils import now_ts
from memory_agent_tool.scoring import normalize_text

logger = get_logger("conflict_service")


def row_to_ingested_memory(row: dict[str, Any], details: str) -> IngestedMemory:
    return IngestedMemory(
        memory_id=int(row["memory_id"]),
        state=MemoryState(row["state"]),
        durability_level=DurabilityLevel(row["durability_level"]),
        trust_score=float(row["trust_score"]),
        conflict_state=ConflictState(row["conflict_state"]),
        rule_overlap_state=RuleOverlapState(row["rule_overlap_state"]),
        summary=row["summary"],
        promoted=row["state"] == MemoryState.PROMOTED_TO_SKILL.value,
        details=details,
    )


from memory_agent_tool.models import DurabilityLevel, RuleOverlapState


class ConflictAndFeedbackService:
    def __init__(self, db: Database, providers: ProviderManager, trust: TrustConfig | None = None):
        self.db = db
        self.providers = providers
        self.trust = trust or TrustConfig()

    def detect_conflict(self, project_key: str, fact_key: str, content: str) -> tuple[bool, int | None]:
        rows = self.db.fetchall(
            """
            SELECT memory_id, content
            FROM memory_items
            WHERE project_key = ? AND fact_key = ? AND state = ?
            ORDER BY updated_at DESC
            """,
            (project_key, fact_key, MemoryState.PINNED_ACTIVE.value),
        )
        new_text = normalize_text(content)
        for row in rows:
            if normalize_text(row["content"]) != new_text:
                logger.info("conflict detected: project=%s fact_key=%s existing=%d", project_key, fact_key, row["memory_id"])
                return True, int(row["memory_id"])
        return False, None

    def record_conflict(self, project_key: str, existing_memory_id: int, candidate_memory_id: int, reason: str, resolution: str = "suspected") -> int:
        cursor = self.db.execute(
            """
            INSERT INTO memory_conflicts(project_key, existing_memory_id, candidate_memory_id, resolution, reason, resolved_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (project_key, existing_memory_id, candidate_memory_id, resolution, reason, now_ts(), now_ts()),
        )
        logger.info("conflict recorded: project=%s existing=%d candidate=%d resolution=%s", project_key, existing_memory_id, candidate_memory_id, resolution)
        return int(cursor.lastrowid)

    def _write_edge(self, project_key: str, from_memory_id: int, to_memory_id: int, relation_type: str) -> None:
        existing = self.db.fetchone(
            """
            SELECT edge_id
            FROM memory_edges
            WHERE project_key = ? AND from_memory_id = ? AND to_memory_id = ? AND relation_type = ?
            """,
            (project_key, from_memory_id, to_memory_id, relation_type),
        )
        if existing is not None:
            return
        self.db.execute(
            """
            INSERT INTO memory_edges(project_key, from_memory_id, to_memory_id, relation_type, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (project_key, from_memory_id, to_memory_id, relation_type, now_ts()),
        )

    def _set_memory_state(
        self,
        memory_id: int,
        *,
        state: str | None = None,
        conflict_state: str | None = None,
        trust_score: float | None = None,
        last_verified_at: float | None = None,
        promotion_state: str | None = None,
    ) -> None:
        row = self.db.fetchone("SELECT * FROM memory_items WHERE memory_id = ?", (memory_id,))
        if row is None:
            return
        self.db.execute(
            """
            UPDATE memory_items
            SET state = ?, conflict_state = ?, trust_score = ?, last_verified_at = ?, promotion_state = ?, updated_at = ?
            WHERE memory_id = ?
            """,
            (
                state or row["state"],
                conflict_state or row["conflict_state"],
                trust_score if trust_score is not None else row["trust_score"],
                last_verified_at if last_verified_at is not None else row["last_verified_at"],
                promotion_state or row["promotion_state"],
                now_ts(),
                memory_id,
            ),
        )

    def resolve_conflict(self, project_key: str, existing_memory_id: int, candidate_memory_id: int) -> ConflictResolutionResult:
        existing = self.db.fetchone("SELECT * FROM memory_items WHERE memory_id = ?", (existing_memory_id,))
        candidate = self.db.fetchone("SELECT * FROM memory_items WHERE memory_id = ?", (candidate_memory_id,))
        if existing is None or candidate is None:
            raise KeyError("conflict memory items not found")
        holographic = self.providers.get("holographic_like")
        decision = holographic.check_conflict(
            current_trust=float(existing["trust_score"]),
            candidate_trust=float(candidate["trust_score"]),
            existing_updated_at=float(existing["updated_at"]),
            candidate_updated_at=float(candidate["updated_at"]),
        )
        resolution = "suspected"
        if decision == "supersede" and (float(candidate["trust_score"]) >= self.trust.auto_promote_threshold or float(existing["trust_score"]) <= self.trust.degrade_threshold):
            resolution = "superseded"
            self._set_memory_state(
                existing_memory_id,
                state=MemoryState.DEGRADED.value,
                conflict_state=ConflictState.SUPERSEDED.value,
            )
            self._set_memory_state(
                candidate_memory_id,
                state=MemoryState.PINNED_ACTIVE.value,
                conflict_state=ConflictState.CONFIRMED.value,
                last_verified_at=now_ts(),
            )
            self._write_edge(project_key, candidate_memory_id, existing_memory_id, "contradicts")
            self._write_edge(project_key, candidate_memory_id, existing_memory_id, "supersedes")
            self.db.execute(
                """
                UPDATE memory_conflicts
                SET resolution = ?, resolved_at = ?
                WHERE project_key = ? AND existing_memory_id = ? AND candidate_memory_id = ?
                """,
                (resolution, now_ts(), project_key, existing_memory_id, candidate_memory_id),
            )
        elif decision == "keep_existing":
            resolution = "confirmed"
            self._set_memory_state(
                candidate_memory_id,
                state=MemoryState.DEGRADED.value,
                conflict_state=ConflictState.CONFIRMED.value,
            )
            self._write_edge(project_key, candidate_memory_id, existing_memory_id, "contradicts")
            self.db.execute(
                """
                UPDATE memory_conflicts
                SET resolution = ?, resolved_at = ?
                WHERE project_key = ? AND existing_memory_id = ? AND candidate_memory_id = ?
                """,
                (resolution, now_ts(), project_key, existing_memory_id, candidate_memory_id),
            )
        else:
            self._set_memory_state(candidate_memory_id, state=MemoryState.CONFLICT_CANDIDATE.value, conflict_state=ConflictState.SUSPECTED.value)
            self.db.execute(
                """
                UPDATE memory_conflicts
                SET resolution = ?, resolved_at = ?
                WHERE project_key = ? AND existing_memory_id = ? AND candidate_memory_id = ?
                """,
                ("suspected", now_ts(), project_key, existing_memory_id, candidate_memory_id),
            )
        logger.info("conflict resolved: project=%s decision=%s resolution=%s", project_key, decision, resolution)
        return ConflictResolutionResult(decision=decision, resolution=resolution)

    def apply_feedback(self, request: FeedbackRequest) -> IngestedMemory:
        row = self.db.fetchone(
            """
            SELECT *
            FROM memory_items
            WHERE memory_id = ?
            """,
            (request.memory_id,),
        )
        if row is None:
            raise KeyError(f"memory_id {request.memory_id} not found")
        positive = int(row["feedback_positive_count"]) + (1 if request.helpful else 0)
        negative = int(row["feedback_negative_count"]) + (0 if request.helpful else 1)
        holographic = self.providers.get("holographic_like")
        trust_score = holographic.adjust_trust(request.helpful, float(row["trust_score"]))
        state = row["state"]
        last_verified_at = row["last_verified_at"]
        if not request.helpful and trust_score <= self.trust.low_trust_threshold:
            state = MemoryState.DEGRADED.value
        if request.helpful:
            last_verified_at = now_ts()
        promotion_state = row["promotion_state"]
        if request.helpful and positive >= self.trust.min_positive_feedback and promotion_state in {PromotionState.NONE.value, PromotionState.CANDIDATE.value}:
            promotion_state = PromotionState.ACCEPTED.value
        self.db.execute(
            """
            UPDATE memory_items
            SET feedback_positive_count = ?, feedback_negative_count = ?, trust_score = ?, state = ?, last_verified_at = ?, promotion_state = ?, updated_at = ?
            WHERE memory_id = ?
            """,
            (positive, negative, trust_score, state, last_verified_at, promotion_state, now_ts(), request.memory_id),
        )
        updated = dict(self.db.fetchone("SELECT * FROM memory_items WHERE memory_id = ?", (request.memory_id,)))
        logger.info("feedback applied: memory_id=%d helpful=%s trust=%.2f", request.memory_id, request.helpful, trust_score)
        return row_to_ingested_memory(updated, "Feedback applied.")
