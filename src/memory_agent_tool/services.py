from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from memory_agent_tool.config import AppSettings
from memory_agent_tool.database import Database
from memory_agent_tool.gateway import ClientRegistry
from memory_agent_tool.mcp import CodexMCPServer
from memory_agent_tool.models import (
    ConflictState,
    ContextBundle,
    DurabilityLevel,
    FeedbackRequest,
    HealthResponse,
    IngestedMemory,
    MemoryIngestRequest,
    MemoryRecallRequest,
    MemoryState,
    PromotionState,
    ProjectAliasRequest,
    RuleOverlapState,
    SessionEvent,
    SessionEndResponse,
    SessionStartRequest,
    SessionStartResponse,
    RecallCandidate,
    SessionSummary,
    SkillFeedbackRequest,
    SkillPromotionRequest,
    SkillSummary,
    StatusReport,
)
from memory_agent_tool.providers import ProviderManager
from memory_agent_tool.resolver import ProjectResolver
from memory_agent_tool.rules import LoadedRules, RulesLoader


def now_ts() -> float:
    return time.time()


def normalize_text(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def summarize_text(value: str, limit: int = 180) -> str:
    collapsed = " ".join((value or "").split())
    return collapsed[:limit]


def extract_fact_key(title: str | None, content: str) -> str:
    if title:
        return normalize_text(title)[:80]
    if ":" in content:
        return normalize_text(content.split(":", 1)[0])[:80]
    words = normalize_text(content).split()
    return " ".join(words[:4])[:80]


def score_for_feedback(helpful: bool, current: float) -> float:
    delta = 0.15 if helpful else -0.2
    return max(0.0, min(1.0, round(current + delta, 2)))


def build_focused_summary(messages: list[dict[str, Any]], query: str | None = None, limit: int = 3) -> str:
    normalized_query = normalize_text(query or "")
    ranked: list[tuple[int, str]] = []
    for message in messages:
        text = message.get("normalized_summary") or summarize_text(message.get("content") or "")
        haystack = normalize_text(text)
        score = 0
        if normalized_query:
            score = sum(1 for token in normalized_query.split() if token in haystack)
        ranked.append((score, text))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    selected = [text for _, text in ranked[:limit] if text]
    if not selected:
        selected = [summarize_text(message.get("content") or "") for message in messages[:limit]]
    return "; ".join(selected)


def overlap_score(query: str, text: str) -> float:
    query_tokens = [token for token in normalize_text(query).split() if token]
    if not query_tokens:
        return 0.0
    haystack = normalize_text(text)
    matched = sum(1 for token in query_tokens if token in haystack)
    return matched / max(len(query_tokens), 1)


def freshness_score(updated_at: float | None, verified_at: float | None) -> float:
    reference = verified_at or updated_at or 0.0
    if not reference:
        return 0.0
    age_days = max(0.0, (now_ts() - reference) / 86400)
    if age_days <= 1:
        return 1.0
    if age_days <= 7:
        return 0.8
    if age_days <= 30:
        return 0.5
    return 0.2


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

    def alias_summary(self) -> dict[str, Any]:
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
        return {
            "alias_count": int(count_row["count"]) if count_row else 0,
            "recent_aliases": [dict(row) for row in alias_rows],
            "recent_alias_usage": latest_usage,
        }


class ConflictAndFeedbackService:
    def __init__(self, db: Database, providers: ProviderManager):
        self.db = db
        self.providers = providers

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

    def resolve_conflict(self, project_key: str, existing_memory_id: int, candidate_memory_id: int) -> dict[str, Any]:
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
        if decision == "supersede" and (float(candidate["trust_score"]) >= 0.75 or float(existing["trust_score"]) <= 0.4):
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
        return {"decision": decision, "resolution": resolution}

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
        if not request.helpful and trust_score <= 0.2:
            state = MemoryState.DEGRADED.value
        if request.helpful:
            last_verified_at = now_ts()
        promotion_state = row["promotion_state"]
        if request.helpful and positive >= 2 and promotion_state in {PromotionState.NONE.value, PromotionState.CANDIDATE.value}:
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
        return row_to_ingested_memory(updated, "Feedback applied.")


class ProjectMemoryService:
    def __init__(self, db: Database, settings: AppSettings, conflicts: ConflictAndFeedbackService):
        self.db = db
        self.settings = settings
        self.conflicts = conflicts

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
            return row_to_ingested_memory(existing, "Duplicate content rejected.")

        state = MemoryState.SESSION_ONLY
        conflict_state = ConflictState.NONE
        trust_score = 0.6
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
            details = f"Conflict resolution: {resolution['resolution']}."

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
        return row_to_ingested_memory(row, details)


class SessionArchiveService:
    def __init__(self, db: Database, projects: ProjectRegistry, rules: RulesLoader, memory: ProjectMemoryService, providers: ProviderManager):
        self.db = db
        self.projects = projects
        self.rules = rules
        self.memory = memory
        self.providers = providers

    def start_session(self, request: SessionStartRequest) -> SessionStartResponse:
        resolved = self.projects.ensure_project(request.project)
        session_id = str(uuid.uuid4())
        self.db.execute(
            """
            INSERT INTO project_sessions(
                session_id, project_key, source_tool, source_channel, started_at, updated_at, status
            )
            VALUES (?, ?, ?, ?, ?, ?, 'active')
            """,
            (
                session_id,
                resolved.project_key,
                request.project.tool_name,
                request.source_channel,
                now_ts(),
                now_ts(),
            ),
        )
        self.db.execute(
            """
            INSERT INTO client_connections(
                client_type, client_session_id, session_id, project_key, source_tool, connected_at, last_heartbeat_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request.project.client_type or "unknown",
                request.project.client_session_id,
                session_id,
                resolved.project_key,
                request.project.tool_name,
                now_ts(),
                now_ts(),
            ),
        )
        return SessionStartResponse(session_id=session_id, resolved_project=resolved)

    def append_event(self, session_id: str, event: SessionEvent, working_context) -> dict[str, Any]:
        session = self.db.fetchone(
            "SELECT * FROM project_sessions WHERE session_id = ?",
            (session_id,),
        )
        if session is None:
            raise KeyError(f"session_id {session_id} not found")
        project_key = session["project_key"]
        cursor = self.db.execute(
            """
            INSERT INTO project_messages(
                session_id, project_key, role_or_event_type, content, normalized_summary,
                created_at, capture_eligible, recalled_from_memory, source_tool
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                project_key,
                event.role_or_event_type,
                event.content,
                event.normalized_summary or summarize_text(event.content),
                now_ts(),
                1 if event.capture_eligible else 0,
                1 if event.recalled_from_memory else 0,
                event.source_tool or session["source_tool"],
            ),
        )
        self.db.execute(
            """
            UPDATE project_sessions
            SET message_count = message_count + 1, updated_at = ?
            WHERE session_id = ?
            """,
            (now_ts(), session_id),
        )
        self._refresh_session_summary(project_key, session_id)
        self.providers.sync_turn(
            project_key,
            {
                "session_id": session_id,
                "message_id": int(cursor.lastrowid),
                "event_type": event.role_or_event_type,
                "content": event.content,
                "normalized_summary": event.normalized_summary or summarize_text(event.content),
            },
        )
        ingested = None
        if event.capture_eligible:
            loaded_rules = self.rules.load(working_context)
            ingested = self.memory.ingest(
                project_key=project_key,
                content=event.content,
                memory_type=event.memory_type,
                title=event.title,
                loaded_rules=loaded_rules,
                source_kind="session_event",
                source_session_id=session_id,
                source_message_id=int(cursor.lastrowid),
                recalled_from_memory=event.recalled_from_memory,
            )
            self.providers.on_memory_write(
                project_key,
                {
                    "memory_id": ingested.memory_id,
                    "state": ingested.state.value,
                    "content": event.content,
                    "source_session_id": session_id,
                    "recall_capture_guard": bool(event.recalled_from_memory),
                },
            )
        return {
            "message_id": int(cursor.lastrowid),
            "project_key": project_key,
            "ingested_memory": ingested.model_dump() if ingested else None,
        }

    def _refresh_session_summary(self, project_key: str, session_id: str) -> None:
        rows = self.db.fetchall(
            """
            SELECT normalized_summary, content
            FROM project_messages
            WHERE session_id = ?
            ORDER BY created_at ASC
            LIMIT 5
            """,
            (session_id,),
        )
        summary = build_focused_summary([dict(row) for row in rows])
        existing = self.db.fetchone(
            "SELECT summary_id FROM session_summaries WHERE project_key = ? AND session_id = ?",
            (project_key, session_id),
        )
        if existing is None:
            self.db.execute(
                """
                INSERT INTO session_summaries(project_key, session_id, summary, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (project_key, session_id, summary, now_ts()),
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

    def _extract_session_candidates(self, project_key: str, session_id: str, working_context) -> list[int]:
        rows = self.db.fetchall(
            """
            SELECT message_id, content, role_or_event_type, normalized_summary
            FROM project_messages
            WHERE session_id = ? AND capture_eligible = 1 AND recalled_from_memory = 0
            ORDER BY created_at ASC
            """,
            (session_id,),
        )
        loaded_rules = self.rules.load(working_context)
        created_ids: list[int] = []
        seen = set()
        for row in rows:
            content = row["content"]
            normalized = normalize_text(content)
            if normalized in seen:
                continue
            seen.add(normalized)
            inferred_type = "procedure" if any(token in normalized for token in ("run ", "command", "procedure", "always")) else "fact"
            durability = self.memory.classify_durability(content, inferred_type)
            if durability == DurabilityLevel.TRANSIENT:
                continue
            ingested = self.memory.ingest(
                project_key=project_key,
                content=content,
                memory_type=inferred_type,
                title=row["normalized_summary"],
                loaded_rules=loaded_rules,
                source_kind="session_end_extraction",
                source_session_id=session_id,
                source_message_id=int(row["message_id"]),
                recalled_from_memory=False,
            )
            if ingested.memory_id not in created_ids:
                created_ids.append(ingested.memory_id)
        return created_ids

    def _link_same_issue_edges(self, project_key: str, session_id: str) -> None:
        summary_row = self.db.fetchone(
            "SELECT summary FROM session_summaries WHERE project_key = ? AND session_id = ?",
            (project_key, session_id),
        )
        if summary_row is None:
            return
        summary = normalize_text(summary_row["summary"])
        if not summary:
            return
        recent = self.db.fetchall(
            """
            SELECT session_id, summary
            FROM session_summaries
            WHERE project_key = ? AND session_id != ?
            ORDER BY updated_at DESC
            LIMIT 5
            """,
            (project_key, session_id),
        )
        current_memories = self.db.fetchall(
            """
            SELECT memory_id, summary
            FROM memory_items
            WHERE project_key = ? AND source_session_id = ?
            """,
            (project_key, session_id),
        )
        for recent_row in recent:
            other_summary = normalize_text(recent_row["summary"])
            if not other_summary:
                continue
            overlap = set(summary.split()) & set(other_summary.split())
            if len(overlap) < 2:
                continue
            older_memories = self.db.fetchall(
                """
                SELECT memory_id
                FROM memory_items
                WHERE project_key = ? AND source_session_id = ?
                """,
                (project_key, recent_row["session_id"]),
            )
            for current in current_memories[:3]:
                for older in older_memories[:3]:
                    existing = self.db.fetchone(
                        """
                        SELECT edge_id
                        FROM memory_edges
                        WHERE project_key = ? AND from_memory_id = ? AND to_memory_id = ? AND relation_type = 'same_issue'
                        """,
                        (project_key, current["memory_id"], older["memory_id"]),
                    )
                    if existing is None:
                        self.db.execute(
                            """
                            INSERT INTO memory_edges(project_key, from_memory_id, to_memory_id, relation_type, created_at)
                            VALUES (?, ?, ?, 'same_issue', ?)
                            """,
                            (project_key, current["memory_id"], older["memory_id"], now_ts()),
                        )

    def end_session(self, session_id: str, working_context) -> dict[str, Any]:
        session = self.db.fetchone("SELECT * FROM project_sessions WHERE session_id = ?", (session_id,))
        if session is None:
            raise KeyError(f"session_id {session_id} not found")
        project_key = session["project_key"]
        rows = self.db.fetchall(
            """
            SELECT role_or_event_type, content, normalized_summary
            FROM project_messages
            WHERE session_id = ?
            ORDER BY created_at ASC
            """,
            (session_id,),
        )
        focused_summary = build_focused_summary([dict(row) for row in rows])
        existing = self.db.fetchone(
            "SELECT summary_id FROM session_summaries WHERE project_key = ? AND session_id = ?",
            (project_key, session_id),
        )
        if existing is None:
            self.db.execute(
                """
                INSERT INTO session_summaries(project_key, session_id, summary, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (project_key, session_id, focused_summary, now_ts()),
            )
        else:
            self.db.execute(
                """
                UPDATE session_summaries
                SET summary = ?, updated_at = ?
                WHERE summary_id = ?
                """,
                (focused_summary, now_ts(), existing["summary_id"]),
            )
        extracted_memory_ids = self._extract_session_candidates(project_key, session_id, working_context)
        self._link_same_issue_edges(project_key, session_id)
        self.db.execute(
            """
            UPDATE project_sessions
            SET status = 'ended', ended_at = ?, updated_at = ?
            WHERE session_id = ?
            """,
            (now_ts(), now_ts(), session_id),
        )
        self.providers.on_session_end(project_key, session_id)
        response = SessionEndResponse(
            session_id=session_id,
            project_key=project_key,
            status="ended",
            focused_summary=focused_summary,
            extracted_memory_ids=extracted_memory_ids,
        )
        return response.model_dump()

    def _fts_query(self, query: str) -> str:
        tokens = []
        for raw in query.split():
            token = "".join(ch for ch in raw if ch.isalnum() or ch in {"_", "-", "."})
            if token:
                tokens.append(f'"{token}"')
        return " OR ".join(tokens) if tokens else '""'

    def search_sessions(self, project_key: str, query: str, limit: int = 3) -> list[SessionSummary]:
        cached = self.db.fetchall(
            """
            SELECT session_id, summary
            FROM session_summaries
            WHERE project_key = ?
            ORDER BY updated_at DESC
            """,
            (project_key,),
        )
        cached_summaries = [
            SessionSummary(
                session_id=row["session_id"],
                source_tool="cached",
                summary=row["summary"],
                matched_messages=[row["summary"]],
            )
            for row in cached
            if any(token in normalize_text(row["summary"]) for token in normalize_text(query).split())
        ]
        if cached_summaries:
            return cached_summaries[:limit]
        fts_query = self._fts_query(query)
        rows = self.db.fetchall(
            """
            SELECT
                s.session_id,
                s.source_tool,
                m.content,
                m.normalized_summary,
                m.created_at,
                bm25(project_messages_fts) AS rank
            FROM project_messages_fts
            JOIN project_messages m ON m.message_id = project_messages_fts.rowid
            JOIN project_sessions s ON s.session_id = m.session_id
            WHERE s.project_key = ? AND project_messages_fts MATCH ?
            ORDER BY rank
            LIMIT 25
            """,
            (project_key, fts_query),
        )
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            group = grouped.setdefault(
                row["session_id"],
                {
                    "source_tool": row["source_tool"],
                    "matched_messages": [],
                },
            )
            if len(group["matched_messages"]) < 3:
                group["matched_messages"].append(row["normalized_summary"] or summarize_text(row["content"]))
        summaries: list[SessionSummary] = []
        for session_id, info in list(grouped.items())[:limit]:
            summary = build_focused_summary(
                [{"normalized_summary": item, "content": item} for item in info["matched_messages"]],
                query=query,
            )
            existing = self.db.fetchone(
                "SELECT summary_id FROM session_summaries WHERE project_key = ? AND session_id = ?",
                (project_key, session_id),
            )
            if existing is None:
                self.db.execute(
                    """
                    INSERT INTO session_summaries(project_key, session_id, summary, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (project_key, session_id, summary, now_ts()),
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
            summaries.append(
                SessionSummary(
                    session_id=session_id,
                    source_tool=info["source_tool"],
                    summary=summary,
                    matched_messages=info["matched_messages"],
                )
            )
        return summaries


class SkillPromotionService:
    def __init__(self, db: Database, settings: AppSettings):
        self.db = db
        self.settings = settings

    def promote(self, project_key: str, memory_id: int, min_positive_feedback: int = 2) -> SkillSummary:
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
        if int(row["feedback_positive_count"]) < min_positive_feedback:
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
        return self.get_skill(skill_id)

    def record_skill_feedback(self, skill_id: int, helpful: bool, accepted: bool = False) -> dict[str, Any]:
        row = self.db.fetchone("SELECT * FROM project_skills WHERE skill_id = ?", (skill_id,))
        if row is None:
            raise KeyError(f"skill_id {skill_id} not found")
        positive = int(row["feedback_positive_count"]) + (1 if helpful else 0)
        negative = int(row["feedback_negative_count"]) + (0 if helpful else 1)
        status = row["status"]
        if not helpful and negative >= 2:
            status = "candidate_refresh"
        self.db.execute(
            """
            UPDATE project_skills
            SET feedback_positive_count = ?, feedback_negative_count = ?, last_used_at = ?, status = ?, updated_at = ?
            WHERE skill_id = ?
            """,
            (positive, negative, now_ts(), status, now_ts(), skill_id),
        )
        return {
            "skill_id": skill_id,
            "feedback_positive_count": positive,
            "feedback_negative_count": negative,
            "status": status,
            "accepted": accepted,
        }

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
        return refreshed_skill.model_copy(
            update={"rationale": f"refreshed from {len(chosen_sources)} source memories"}
        )

    def auto_promote(self, project_key: str, min_positive_feedback: int = 2) -> list[SkillSummary]:
        rows = self.db.fetchall(
            """
            SELECT memory_id
            FROM memory_items
            WHERE project_key = ?
              AND feedback_positive_count >= ?
              AND trust_score >= 0.75
              AND promotion_state IN (?, ?)
              AND state = ?
            """,
            (
                project_key,
                min_positive_feedback,
                PromotionState.NONE.value,
                PromotionState.CANDIDATE.value,
                MemoryState.PINNED_ACTIVE.value,
            ),
        )
        promoted = []
        for row in rows:
            promoted.append(self.promote(project_key, int(row["memory_id"]), min_positive_feedback))
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

    def observability_summary(self) -> dict[str, Any]:
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
        return {
            "total_skills": int(totals["count"]) if totals else 0,
            "candidate_refresh_count": int(totals["candidate_refresh_count"] or 0) if totals else 0,
            "refreshed_skill_count": int(totals["refreshed_count"] or 0) if totals else 0,
            "latest_refresh": dict(latest_refresh) if latest_refresh else None,
        }


class MemoryMaintenanceService:
    def __init__(self, db: Database, memory: ProjectMemoryService, archive: SessionArchiveService):
        self.db = db
        self.memory = memory
        self.archive = archive

    def _record_run(self, project_key: str | None, action: str, result: dict[str, Any]) -> None:
        self.db.execute(
            """
            INSERT INTO maintenance_runs(project_key, action, result_json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (project_key, action, json.dumps(result, ensure_ascii=False), now_ts()),
        )

    def review_stale_memories(self, project_key: str) -> dict[str, Any]:
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
            if freshness <= 0.2 and trust < 0.55:
                review_candidates += 1
                new_state = MemoryState.DEGRADED.value if trust < 0.4 else MemoryState.SESSION_ONLY.value
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
        result = {"project_key": project_key, "review_candidates": review_candidates, "degraded": degraded}
        self._record_run(project_key, "review_stale_memories", result)
        return result

    def consolidate_project_memory(self, project_key: str) -> dict[str, Any]:
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
        result = {"project_key": project_key, "consolidated": consolidated}
        self._record_run(project_key, "consolidate_project_memory", result)
        return result

    def rebuild_session_summaries(self, project_key: str | None = None) -> dict[str, Any]:
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
        result = {"project_key": project_key, "rebuilt": rebuilt}
        self._record_run(project_key, "rebuild_session_summaries", result)
        return result


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
    ):
        self.db = db
        self.rules = rules
        self.projects = projects
        self.memory = memory
        self.archive = archive
        self.skills = skills
        self.providers = providers

    def _score_memory_row(self, query: str, row: dict[str, Any]) -> float:
        text_score = overlap_score(query, f"{row.get('title') or ''} {row.get('summary') or ''} {row.get('content') or ''}")
        trust_score = float(row.get("trust_score") or 0.0)
        freshness = freshness_score(row.get("updated_at"), row.get("last_verified_at"))
        state_bonus = 0.2 if row.get("state") == MemoryState.PINNED_ACTIVE.value else -0.3
        conflict_penalty = {
            ConflictState.NONE.value: 0.0,
            ConflictState.CONFIRMED.value: -0.1,
            ConflictState.SUSPECTED.value: -0.3,
            ConflictState.SUPERSEDED.value: -0.7,
        }.get(row.get("conflict_state"), -0.2)
        return round((text_score * 0.55) + (trust_score * 0.25) + (freshness * 0.15) + state_bonus + conflict_penalty, 4)

    def _score_session_summary(self, query: str, summary: SessionSummary) -> float:
        return round((overlap_score(query, f"{summary.summary} {' '.join(summary.matched_messages)}") * 0.75) + 0.15, 4)

    def _score_skill(self, query: str, skill: SkillSummary) -> float:
        return round((overlap_score(query, f"{skill.name} {skill.content}") * 0.8) + 0.1, 4)

    def _score_provider_context(self, query: str, snippet: str) -> float:
        return round((overlap_score(query, snippet) * 0.45) + 0.05, 4)

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
            if overlap_score(query, haystack) > 0:
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
        top_memory_rows = [candidate.details for candidate in memory_candidates[: budget["memory"]] if candidate.score > 0.05]
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
            if candidate.score > 0.05
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
            if candidate.score <= 0.05:
                continue
            skill = candidate.details["skill"].model_copy(
                update={
                    "relevance_score": candidate.score,
                    "rationale": f"query overlap={round(overlap_score(request.query, candidate.summary), 2)}",
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
        provider_context = [candidate.details["snippet"] for candidate in provider_candidates[: budget["providers"]] if candidate.score > 0.05]
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
        skill_observability = self.skills.observability_summary()
        project_scope_observability = self.projects.alias_summary()
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


@dataclass(slots=True)
class AppContainer:
    settings: AppSettings
    db: Database
    resolver: ProjectResolver
    rules_loader: RulesLoader
    providers: ProviderManager
    projects: ProjectRegistry
    conflicts: ConflictAndFeedbackService
    memory: ProjectMemoryService
    archive: SessionArchiveService
    skills: SkillPromotionService
    maintenance: MemoryMaintenanceService
    retrieval: RetrievalPipeline
    reporter: StatusReporter
    client_registry: ClientRegistry
    codex_mcp: CodexMCPServer

    @classmethod
    def build(cls, settings: AppSettings) -> "AppContainer":
        settings.ensure_directories()
        db = Database(settings.db_path)
        resolver = ProjectResolver()
        rules_loader = RulesLoader()
        providers = ProviderManager(db)
        providers.initialize(root_dir=str(settings.root_dir), db_path=str(settings.db_path))
        projects = ProjectRegistry(db, resolver)
        conflicts = ConflictAndFeedbackService(db, providers)
        memory = ProjectMemoryService(db, settings, conflicts)
        archive = SessionArchiveService(db, projects, rules_loader, memory, providers)
        skills = SkillPromotionService(db, settings)
        maintenance = MemoryMaintenanceService(db, memory, archive)
        retrieval = RetrievalPipeline(db, rules_loader, projects, memory, archive, skills, providers)
        reporter = StatusReporter(db, providers, skills, projects)
        skeleton = cls.__new__(cls)
        skeleton.settings = settings
        skeleton.db = db
        skeleton.resolver = resolver
        skeleton.rules_loader = rules_loader
        skeleton.providers = providers
        skeleton.projects = projects
        skeleton.conflicts = conflicts
        skeleton.memory = memory
        skeleton.archive = archive
        skeleton.skills = skills
        skeleton.maintenance = maintenance
        skeleton.retrieval = retrieval
        skeleton.reporter = reporter
        skeleton.client_registry = ClientRegistry(skeleton)
        skeleton.codex_mcp = CodexMCPServer(skeleton)
        return skeleton


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
