from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from memory_agent_tool.config import AppSettings, TrustConfig
from memory_agent_tool.database import Database
from memory_agent_tool.logging import get_logger
from memory_agent_tool.models import (
    ConflictState,
    MemoryState,
    ObservabilitySummaryResult,
    PromotionState,
    SkillFeedbackResult,
    SkillSummary,
)
from memory_agent_tool.scoring import normalize_text
from memory_agent_tool.services.utils import now_ts, summarize_text

logger = get_logger("skill_service")


class SkillPromotionService:
    def __init__(self, db: Database, settings: AppSettings, trust: TrustConfig | None = None):
        self.db = db
        self.settings = settings
        self.trust = trust or settings.trust

    def promote(self, project_key: str, memory_id: int, min_positive_feedback: int | None = None) -> SkillSummary:
        min_fb = min_positive_feedback if min_positive_feedback is not None else self.trust.min_positive_feedback
        row = self.db.fetchone(
            """
            SELECT *
            FROM memory_items
            WHERE memory_id = ? AND project_key = ?
            """,
            (memory_id, project_key),
        )
        if row is None:
            raise KeyError(f"memory_id {memory_id} not found")
        if int(row["feedback_positive_count"]) < min_fb:
            raise ValueError("Not enough positive feedback to promote")
        name = row["title"] or summarize_text(row["content"], limit=48).replace(" ", "-")
        skill_path = self.settings.skills_dir / project_key.replace("::", "__")
        skill_path.mkdir(parents=True, exist_ok=True)
        file_path = skill_path / f"{name[:48].replace('/', '-')}.md"
        content = (
            f"# {row['title'] or 'Project Skill'}\n\n"
            f"Source memory #{memory_id}\n\n"
            f"{row['content']}\n"
        )
        file_path.write_text(content, encoding="utf-8")
        ts = now_ts()
        cursor = self.db.execute(
            """
            INSERT INTO project_skills(
                project_key, name, content, file_path, status, version, source_memory_count, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 'active', 1, 1, ?, ?)
            """,
            (project_key, name, row["content"], str(file_path), ts, ts),
        )
        skill_id = int(cursor.lastrowid)
        self.db.execute(
            """
            INSERT INTO skill_sources(skill_id, memory_id, created_at)
            VALUES (?, ?, ?)
            """,
            (skill_id, memory_id, ts),
        )
        existing_edge = self.db.fetchone(
            """
            SELECT edge_id
            FROM memory_edges
            WHERE project_key = ? AND from_memory_id = ? AND to_memory_id = ? AND relation_type = 'derived_to_skill'
            """,
            (project_key, memory_id, memory_id),
        )
        if existing_edge is None:
            self.db.execute(
                """
                INSERT INTO memory_edges(project_key, from_memory_id, to_memory_id, relation_type, created_at)
                VALUES (?, ?, ?, 'derived_to_skill', ?)
                """,
                (project_key, memory_id, memory_id, ts),
            )
        self.db.execute(
            """
            UPDATE memory_items
            SET state = ?, promotion_state = ?, last_verified_at = ?, updated_at = ?
            WHERE memory_id = ?
            """,
            (MemoryState.PROMOTED_TO_SKILL.value, PromotionState.PROMOTED.value, ts, ts, memory_id),
        )
        logger.info("skill promoted: project=%s memory_id=%d skill_id=%d name=%s", project_key, memory_id, skill_id, name)
        return self.get_skill(skill_id)

    def record_skill_feedback(self, skill_id: int, helpful: bool, accepted: bool = False) -> SkillFeedbackResult:
        row = self.db.fetchone("SELECT * FROM project_skills WHERE skill_id = ?", (skill_id,))
        if row is None:
            raise KeyError(f"skill_id {skill_id} not found")
        positive = int(row["feedback_positive_count"]) + (1 if helpful else 0)
        negative = int(row["feedback_negative_count"]) + (0 if helpful else 1)
        status = row["status"]
        if not helpful and negative >= self.trust.min_negative_for_refresh:
            status = "candidate_refresh"
        self.db.execute(
            """
            UPDATE project_skills
            SET feedback_positive_count = ?, feedback_negative_count = ?, last_used_at = ?, status = ?, updated_at = ?
            WHERE skill_id = ?
            """,
            (positive, negative, now_ts(), status, now_ts(), skill_id),
        )
        logger.info("skill feedback: skill_id=%d helpful=%s status=%s", skill_id, helpful, status)
        return SkillFeedbackResult(
            skill_id=skill_id,
            feedback_positive_count=positive,
            feedback_negative_count=negative,
            status=status,
            accepted=accepted,
        )

    def get_skill(self, skill_id: int) -> SkillSummary:
        row = self.db.fetchone("SELECT * FROM project_skills WHERE skill_id = ?", (skill_id,))
        if row is None:
            raise KeyError(f"skill_id {skill_id} not found")
        source_rows = self.db.fetchall(
            """
            SELECT memory_id
            FROM skill_sources
            WHERE skill_id = ?
            ORDER BY created_at ASC
            """,
            (skill_id,),
        )
        return SkillSummary(
            skill_id=int(row["skill_id"]),
            name=row["name"],
            content=row["content"],
            file_path=row["file_path"],
            status=row["status"],
            version=int(row["version"] or 1),
            feedback_positive_count=int(row["feedback_positive_count"] or 0),
            feedback_negative_count=int(row["feedback_negative_count"] or 0),
            source_memory_count=int(row["source_memory_count"] or len(source_rows)),
            source_memory_ids=[int(item["memory_id"]) for item in source_rows],
            last_used_at=(
                datetime.fromtimestamp(float(row["last_used_at"]), tz=timezone.utc)
                if row["last_used_at"] is not None
                else None
            ),
            last_refreshed_at=(
                datetime.fromtimestamp(float(row["last_refreshed_at"]), tz=timezone.utc)
                if row["last_refreshed_at"] is not None
                else None
            ),
        )

    def refresh_skill_from_sources(self, skill_id: int) -> SkillSummary:
        row = self.db.fetchone("SELECT * FROM project_skills WHERE skill_id = ?", (skill_id,))
        if row is None:
            raise KeyError(f"skill_id {skill_id} not found")
        sources = self.db.fetchall(
            """
            SELECT m.memory_id, m.content, m.summary, m.state, m.conflict_state, m.trust_score
            FROM skill_sources s
            JOIN memory_items m ON m.memory_id = s.memory_id
            WHERE s.skill_id = ?
            ORDER BY m.updated_at DESC
            """,
            (skill_id,),
        )
        if not sources:
            raise ValueError("skill has no source memories")
        active_sources = [
            dict(source)
            for source in sources
            if source["state"] != MemoryState.DEGRADED.value and source["conflict_state"] != ConflictState.SUPERSEDED.value
        ]
        chosen_sources = active_sources or [dict(source) for source in sources]
        merged_content = "\n".join(dict.fromkeys(source["content"] for source in chosen_sources))
        status = "active" if active_sources else "candidate_refresh"
        version = int(row["version"]) + 1
        self.db.execute(
            """
            UPDATE project_skills
            SET content = ?, version = ?, status = ?, source_memory_count = ?, last_refreshed_at = ?, updated_at = ?
            WHERE skill_id = ?
            """,
            (merged_content, version, status, len(chosen_sources), now_ts(), now_ts(), skill_id),
        )
        refreshed = self.db.fetchone("SELECT * FROM project_skills WHERE skill_id = ?", (skill_id,))
        Path(refreshed["file_path"]).write_text(
            f"# {refreshed['name']}\n\nVersion: {refreshed['version']}\n\n{refreshed['content']}\n",
            encoding="utf-8",
        )
        refreshed_skill = self.get_skill(skill_id)
        logger.info("skill refreshed: skill_id=%d version=%d sources=%d", skill_id, version, len(chosen_sources))
        return refreshed_skill.model_copy(
            update={"rationale": f"refreshed from {len(chosen_sources)} source memories"}
        )

    def auto_promote(self, project_key: str, min_positive_feedback: int | None = None) -> list[SkillSummary]:
        min_fb = min_positive_feedback if min_positive_feedback is not None else self.trust.min_positive_feedback
        rows = self.db.fetchall(
            """
            SELECT memory_id
            FROM memory_items
            WHERE project_key = ?
              AND feedback_positive_count >= ?
              AND trust_score >= ?
              AND promotion_state IN (?, ?)
              AND state = ?
            """,
            (
                project_key,
                min_fb,
                self.trust.auto_promote_threshold,
                PromotionState.NONE.value,
                PromotionState.CANDIDATE.value,
                MemoryState.PINNED_ACTIVE.value,
            ),
        )
        promoted = []
        for row in rows:
            promoted.append(self.promote(project_key, int(row["memory_id"]), min_fb))
        logger.info("auto_promote: project=%s count=%d", project_key, len(promoted))
        return promoted

    def relevant_skills(self, project_key: str, query: str) -> list[SkillSummary]:
        normalized_query = normalize_text(query)
        rows = self.db.fetchall(
            """
            SELECT *
            FROM project_skills
            WHERE project_key = ? AND status = 'active'
            ORDER BY updated_at DESC
            """,
            (project_key,),
        )
        results = []
        for row in rows:
            haystack = normalize_text(f"{row['name']} {row['content']}")
            if not normalized_query or any(token in haystack for token in normalized_query.split()):
                results.append(
                    self.get_skill(int(row["skill_id"])).model_copy(
                        update={
                            "relevance_score": 0.0,
                            "rationale": (
                                f"feedback+={row['feedback_positive_count']} feedback-={row['feedback_negative_count']}"
                                if "feedback_positive_count" in row.keys()
                                else None
                            ),
                        }
                    )
                )
        return results[:5]

    def observability_summary(self) -> ObservabilitySummaryResult:
        totals = self.db.fetchone(
            """
            SELECT
                COUNT(*) AS count,
                SUM(CASE WHEN status = 'candidate_refresh' THEN 1 ELSE 0 END) AS candidate_refresh_count,
                SUM(CASE WHEN last_refreshed_at IS NOT NULL THEN 1 ELSE 0 END) AS refreshed_count
            FROM project_skills
            """
        )
        latest_refresh = self.db.fetchone(
            """
            SELECT skill_id, name, version, last_refreshed_at, status
            FROM project_skills
            WHERE last_refreshed_at IS NOT NULL
            ORDER BY last_refreshed_at DESC
            LIMIT 1
            """
        )
        return ObservabilitySummaryResult(
            total_skills=int(totals["count"]) if totals else 0,
            candidate_refresh_count=int(totals["candidate_refresh_count"] or 0) if totals else 0,
            refreshed_skill_count=int(totals["refreshed_count"] or 0) if totals else 0,
            latest_refresh=dict(latest_refresh) if latest_refresh else None,
        )
