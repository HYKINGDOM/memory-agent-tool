from __future__ import annotations

from typing import Any

from memory_agent_tool.config import ScoringConfig
from memory_agent_tool.database import Database
from memory_agent_tool.logging import get_logger
from memory_agent_tool.models import (
    ConflictState,
    ContextBundle,
    IngestedMemory,
    MemoryRecallRequest,
    MemoryState,
    RecallCandidate,
    SessionSummary,
    SkillSummary,
)
from memory_agent_tool.providers import ProviderManager
from memory_agent_tool.rules import RulesLoader
from memory_agent_tool.scoring import RecallScorer, create_scorer, normalize_text
from memory_agent_tool.services.conflict_service import row_to_ingested_memory
from memory_agent_tool.services.memory_service import ProjectMemoryService
from memory_agent_tool.services.project_service import ProjectRegistry
from memory_agent_tool.services.session_service import SessionArchiveService
from memory_agent_tool.services.skill_service import SkillPromotionService
from memory_agent_tool.services.utils import freshness_score, now_ts, summarize_text

logger = get_logger("retrieval_service")


class RetrievalPipeline:
    def __init__(
        self,
        db: Database,
        rules: RulesLoader,
        projects: ProjectRegistry,
        memory: ProjectMemoryService,
        archive: SessionArchiveService,
        skills: SkillPromotionService,
        providers: ProviderManager,
        scoring: ScoringConfig | None = None,
    ):
        self.db = db
        self.rules = rules
        self.projects = projects
        self.memory = memory
        self.archive = archive
        self.skills = skills
        self.providers = providers
        self.scoring = scoring or ScoringConfig()
        self._scorer: RecallScorer = create_scorer(self.scoring.strategy)

    def _score_memory_row(self, query: str, row: dict[str, Any]) -> float:
        text_score = self._scorer.score(query, f"{row.get('title') or ''} {row.get('summary') or ''} {row.get('content') or ''}")
        trust_score = float(row.get("trust_score") or 0.0)
        freshness = freshness_score(row.get("updated_at"), row.get("last_verified_at"))
        state_bonus = self.scoring.state_bonus if row.get("state") == MemoryState.PINNED_ACTIVE.value else self.scoring.state_penalty
        conflict_penalty = self.scoring.conflict_penalties.get(
            row.get("conflict_state"), self.scoring.default_conflict_penalty
        )
        return round(
            (text_score * self.scoring.text_weight)
            + (trust_score * self.scoring.trust_weight)
            + (freshness * self.scoring.freshness_weight)
            + state_bonus
            + conflict_penalty,
            4,
        )

    def _score_session_summary(self, query: str, summary: SessionSummary) -> float:
        text_score = self._scorer.score(query, f"{summary.summary} {' '.join(summary.matched_messages)}")
        return round((text_score * 0.75) + 0.15, 4)

    def _score_skill(self, query: str, skill: SkillSummary) -> float:
        text_score = self._scorer.score(query, f"{skill.name} {skill.content}")
        return round((text_score * 0.8) + 0.1, 4)

    def _score_provider_context(self, query: str, snippet: str) -> float:
        text_score = self._scorer.score(query, snippet)
        return round((text_score * 0.45) + 0.05, 4)

    def _query_budget(self, query: str, limit: int) -> dict[str, int]:
        token_count = len(normalize_text(query).split())
        base = max(1, min(limit, 5))
        if token_count >= 6:
            return {"memory": base + 1, "sessions": base, "skills": base, "providers": 2}
        if token_count >= 3:
            return {"memory": base, "sessions": max(1, base - 1), "skills": max(1, base - 1), "providers": 2}
        return {"memory": base, "sessions": 1, "skills": 1, "providers": 1}

    def _conflict_hints_for_query(self, project_key: str, query: str) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            """
            SELECT c.conflict_id, c.existing_memory_id, c.candidate_memory_id, c.resolution, c.reason,
                   existing.summary AS existing_summary,
                   candidate.summary AS candidate_summary
            FROM memory_conflicts c
            LEFT JOIN memory_items existing ON existing.memory_id = c.existing_memory_id
            LEFT JOIN memory_items candidate ON candidate.memory_id = c.candidate_memory_id
            WHERE c.project_key = ?
            ORDER BY c.created_at DESC
            LIMIT 10
            """,
            (project_key,),
        )
        hints = []
        for row in rows:
            haystack = normalize_text(
                f"{row['reason']} {row['resolution']} {row['existing_summary'] or ''} {row['candidate_summary'] or ''}"
            )
            if self._scorer.score(query, haystack) > 0:
                hints.append(dict(row))
        return hints[:3]

    def recall(self, request: MemoryRecallRequest) -> ContextBundle:
        resolved = self.projects.ensure_project(request.project)
        loaded_rules = self.rules.load(request.project)
        active_rows = self.memory.list_active(resolved.project_key)
        memory_candidates = sorted(
            (
                RecallCandidate(
                    source="pinned_memory",
                    source_id=str(row["memory_id"]),
                    score=self._score_memory_row(request.query, row),
                    title=row.get("title") or row.get("fact_key") or "memory",
                    summary=row["summary"],
                    details=row,
                )
                for row in active_rows
            ),
            key=lambda item: item.score,
            reverse=True,
        )
        budget = self._query_budget(request.query, request.limit)
        top_memory_rows = [candidate.details for candidate in memory_candidates[: budget["memory"]] if candidate.score > self.scoring.min_score_threshold]
        fixed_memory = [row_to_ingested_memory(row, "Pinned memory.") for row in top_memory_rows]
        session_summaries = self.archive.search_sessions(resolved.project_key, request.query, request.limit)
        session_candidates = sorted(
            (
                RecallCandidate(
                    source="session_summary",
                    source_id=summary.session_id,
                    score=self._score_session_summary(request.query, summary),
                    title=summary.source_tool,
                    summary=summary.summary,
                    details={"session": summary},
                )
                for summary in session_summaries
            ),
            key=lambda item: item.score,
            reverse=True,
        )
        top_sessions = [
            candidate.details["session"]
            for candidate in session_candidates[: budget["sessions"]]
            if candidate.score > self.scoring.min_score_threshold
        ]
        skill_candidates = sorted(
            (
                RecallCandidate(
                    source="skill",
                    source_id=str(skill.skill_id),
                    score=self._score_skill(request.query, skill),
                    title=skill.name,
                    summary=summarize_text(skill.content),
                    details={"skill": skill},
                )
                for skill in self.skills.relevant_skills(resolved.project_key, request.query)
            ),
            key=lambda item: item.score,
            reverse=True,
        )
        recommended_skills = []
        for candidate in skill_candidates[: budget["skills"]]:
            if candidate.score <= self.scoring.min_score_threshold:
                continue
            skill = candidate.details["skill"].model_copy(
                update={
                    "relevance_score": candidate.score,
                    "rationale": f"query overlap={round(self._scorer.score(request.query, candidate.summary), 2)}",
                }
            )
            recommended_skills.append(skill)
        provider_candidates = sorted(
            (
                RecallCandidate(
                    source="provider",
                    source_id=f"provider-{index}",
                    score=self._score_provider_context(request.query, snippet),
                    title="provider_context",
                    summary=snippet,
                    details={"snippet": snippet},
                )
                for index, snippet in enumerate(self.providers.prefetch(resolved.project_key, request.query))
            ),
            key=lambda item: item.score,
            reverse=True,
        )
        provider_context = [candidate.details["snippet"] for candidate in provider_candidates[: budget["providers"]] if candidate.score > self.scoring.min_score_threshold]
        conflict_hints = self._conflict_hints_for_query(resolved.project_key, request.query)
        self.db.execute(
            """
            INSERT INTO retrieval_logs(project_key, query, used_sessions, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (resolved.project_key, request.query, len(top_sessions), now_ts()),
        )
        combined_parts = []
        if loaded_rules.summaries:
            combined_parts.append("Rules:\n" + "\n\n".join(item.summary for item in loaded_rules.summaries))
        if fixed_memory:
            combined_parts.append(
                "Pinned memory:\n"
                + "\n".join(f"- {item.summary}" for item in fixed_memory)
            )
        if top_sessions:
            combined_parts.append(
                "Related sessions:\n"
                + "\n".join(f"- [{item.source_tool}] {item.summary}" for item in top_sessions)
            )
        if recommended_skills:
            combined_parts.append(
                "Recommended skills:\n"
                + "\n".join(
                    f"- {item.name}: {summarize_text(item.content)}"
                    + (f" ({item.rationale})" if item.rationale else "")
                    for item in recommended_skills
                )
            )
        if provider_context:
            combined_parts.append("Provider context:\n" + "\n".join(provider_context))
        if conflict_hints:
            combined_parts.append(
                "Conflict hints:\n"
                + "\n".join(f"- {hint['reason']}" for hint in conflict_hints)
            )
        if fixed_memory:
            for row in top_memory_rows:
                self.db.execute(
                    "UPDATE memory_items SET last_verified_at = ?, updated_at = ? WHERE memory_id = ?",
                    (now_ts(), row.get("updated_at") or now_ts(), row["memory_id"]),
                )
        logger.info("recall: project=%s query='%s' scorer=%s memory=%d sessions=%d skills=%d", resolved.project_key, request.query[:50], self._scorer.name(), len(fixed_memory), len(top_sessions), len(recommended_skills))
        return ContextBundle(
            rules_summary=loaded_rules.summaries,
            fixed_memory_summary=fixed_memory,
            related_session_summaries=top_sessions,
            recommended_skills=recommended_skills,
            provider_context=provider_context,
            conflict_hints=conflict_hints,
            source_trace=[
                {"source": "rules", "count": len(loaded_rules.summaries)},
                {"source": "fixed_memory", "count": len(fixed_memory)},
                {"source": "session_summaries", "count": len(top_sessions)},
                {"source": "providers", "count": len(provider_context)},
                {"source": "skills", "count": len(recommended_skills)},
            ],
            combined_text="\n\n".join(combined_parts),
        )
