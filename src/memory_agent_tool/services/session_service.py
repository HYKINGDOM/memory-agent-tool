from __future__ import annotations

import uuid
from typing import Any

from memory_agent_tool.database import Database
from memory_agent_tool.logging import get_logger
from memory_agent_tool.models import (
    AppendEventResult,
    DurabilityLevel,
    SessionEndResponse,
    SessionEvent,
    SessionStartRequest,
    SessionStartResponse,
    SessionSummary,
)
from memory_agent_tool.providers import ProviderManager
from memory_agent_tool.rules import RulesLoader
from memory_agent_tool.scoring import normalize_text
from memory_agent_tool.services.project_service import ProjectRegistry
from memory_agent_tool.services.memory_service import ProjectMemoryService
from memory_agent_tool.services.utils import build_focused_summary, now_ts, summarize_text

logger = get_logger("session_service")


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
        logger.info("session started: session_id=%s project=%s", session_id, resolved.project_key)
        return SessionStartResponse(session_id=session_id, resolved_project=resolved)

    def append_event(self, session_id: str, event: SessionEvent, working_context) -> AppendEventResult:
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
        logger.info("event appended: session_id=%s message_id=%d capture=%s", session_id, int(cursor.lastrowid), event.capture_eligible)
        return AppendEventResult(
            message_id=int(cursor.lastrowid),
            project_key=project_key,
            ingested_memory=ingested,
        )

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

    def end_session(self, session_id: str, working_context) -> SessionEndResponse:
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
        logger.info("session ended: session_id=%s project=%s memories=%d", session_id, project_key, len(extracted_memory_ids))
        return SessionEndResponse(
            session_id=session_id,
            project_key=project_key,
            status="ended",
            focused_summary=focused_summary,
            extracted_memory_ids=extracted_memory_ids,
        )

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
