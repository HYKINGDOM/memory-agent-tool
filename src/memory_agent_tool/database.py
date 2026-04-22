from __future__ import annotations

import asyncio
import sqlite3
import time
import threading
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 8

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    project_key TEXT PRIMARY KEY,
    canonical_project_key TEXT,
    display_name TEXT,
    repo_identity TEXT,
    namespace TEXT,
    workspace TEXT,
    branch TEXT,
    monorepo_subpath TEXT,
    scope_components_json TEXT,
    status TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS project_sessions (
    session_id TEXT PRIMARY KEY,
    project_key TEXT NOT NULL REFERENCES projects(project_key),
    source_tool TEXT NOT NULL,
    source_channel TEXT,
    parent_session_id TEXT,
    started_at REAL NOT NULL,
    ended_at REAL,
    updated_at REAL NOT NULL,
    message_count INTEGER DEFAULT 0,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    status TEXT
);

CREATE TABLE IF NOT EXISTS project_messages (
    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES project_sessions(session_id),
    project_key TEXT NOT NULL REFERENCES projects(project_key),
    role_or_event_type TEXT NOT NULL,
    content TEXT NOT NULL,
    normalized_summary TEXT,
    created_at REAL NOT NULL,
    capture_eligible INTEGER NOT NULL DEFAULT 1,
    recalled_from_memory INTEGER NOT NULL DEFAULT 0,
    source_tool TEXT
);

CREATE INDEX IF NOT EXISTS idx_project_sessions_project ON project_sessions(project_key, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_project_messages_project ON project_messages(project_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_project_messages_session ON project_messages(session_id, created_at);

CREATE VIRTUAL TABLE IF NOT EXISTS project_messages_fts USING fts5(
    content,
    content=project_messages,
    content_rowid=message_id
);

CREATE TRIGGER IF NOT EXISTS project_messages_fts_insert AFTER INSERT ON project_messages BEGIN
    INSERT INTO project_messages_fts(rowid, content) VALUES (new.message_id, new.content);
END;

CREATE TRIGGER IF NOT EXISTS project_messages_fts_delete AFTER DELETE ON project_messages BEGIN
    INSERT INTO project_messages_fts(project_messages_fts, rowid, content)
    VALUES('delete', old.message_id, old.content);
END;

CREATE TRIGGER IF NOT EXISTS project_messages_fts_update AFTER UPDATE ON project_messages BEGIN
    INSERT INTO project_messages_fts(project_messages_fts, rowid, content)
    VALUES('delete', old.message_id, old.content);
    INSERT INTO project_messages_fts(rowid, content) VALUES (new.message_id, new.content);
END;

CREATE TABLE IF NOT EXISTS memory_items (
    memory_id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_key TEXT NOT NULL REFERENCES projects(project_key),
    memory_type TEXT NOT NULL,
    title TEXT,
    summary TEXT NOT NULL,
    content TEXT NOT NULL,
    fact_key TEXT,
    source_kind TEXT,
    source_session_id TEXT,
    source_message_id INTEGER,
    state TEXT NOT NULL,
    durability_level TEXT NOT NULL,
    trust_score REAL NOT NULL DEFAULT 0.5,
    feedback_positive_count INTEGER NOT NULL DEFAULT 0,
    feedback_negative_count INTEGER NOT NULL DEFAULT 0,
    conflict_state TEXT NOT NULL DEFAULT 'none',
    rule_overlap_state TEXT NOT NULL DEFAULT 'none',
    recall_capture_guard INTEGER NOT NULL DEFAULT 0,
    last_verified_at REAL,
    promotion_state TEXT NOT NULL DEFAULT 'none',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memory_items_project ON memory_items(project_key, state, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_memory_items_fact_key ON memory_items(project_key, fact_key);

CREATE TABLE IF NOT EXISTS memory_edges (
    edge_id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_key TEXT NOT NULL REFERENCES projects(project_key),
    from_memory_id INTEGER NOT NULL REFERENCES memory_items(memory_id),
    to_memory_id INTEGER NOT NULL REFERENCES memory_items(memory_id),
    relation_type TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS project_skills (
    skill_id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_key TEXT NOT NULL REFERENCES projects(project_key),
    name TEXT NOT NULL,
    content TEXT NOT NULL,
    file_path TEXT NOT NULL,
    status TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    last_used_at REAL,
    last_refreshed_at REAL,
    feedback_positive_count INTEGER NOT NULL DEFAULT 0,
    feedback_negative_count INTEGER NOT NULL DEFAULT 0,
    source_memory_count INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS skill_sources (
    skill_source_id INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_id INTEGER NOT NULL REFERENCES project_skills(skill_id),
    memory_id INTEGER NOT NULL REFERENCES memory_items(memory_id),
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS retrieval_logs (
    retrieval_id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_key TEXT NOT NULL REFERENCES projects(project_key),
    query TEXT NOT NULL,
    used_sessions INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS test_runs (
    test_run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_type TEXT NOT NULL,
    status TEXT NOT NULL,
    report_json TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS client_connections (
    connection_id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_type TEXT NOT NULL,
    client_session_id TEXT,
    session_id TEXT NOT NULL,
    project_key TEXT NOT NULL,
    source_tool TEXT NOT NULL,
    connected_at REAL NOT NULL,
    last_heartbeat_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_runs (
    provider_name TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    capabilities_json TEXT NOT NULL,
    last_error TEXT,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_conflicts (
    conflict_id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_key TEXT NOT NULL,
    existing_memory_id INTEGER NOT NULL,
    candidate_memory_id INTEGER NOT NULL,
    resolution TEXT NOT NULL,
    reason TEXT NOT NULL,
    resolved_at REAL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_name TEXT NOT NULL,
    project_key TEXT NOT NULL,
    session_id TEXT,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_provider_events_project ON provider_events(project_key, provider_name, created_at DESC);

CREATE TABLE IF NOT EXISTS session_summaries (
    summary_id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_key TEXT NOT NULL,
    session_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS service_state (
    state_key TEXT PRIMARY KEY,
    state_value TEXT NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS maintenance_runs (
    maintenance_id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_key TEXT,
    action TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS project_aliases (
    alias_key TEXT PRIMARY KEY,
    canonical_project_key TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_configs (
    config_scope TEXT NOT NULL,
    project_key TEXT NOT NULL DEFAULT '',
    config_json TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (config_scope, project_key)
);
"""


class Database:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=5.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(SCHEMA_SQL)
            self._run_migrations()
            row = self._conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
            if row is None:
                self._conn.execute("INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,))
            else:
                self._conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))
            self._conn.commit()

    def _run_migrations(self) -> None:
        self._ensure_column("projects", "canonical_project_key", "TEXT")
        self._ensure_column("projects", "workspace", "TEXT")
        self._ensure_column("projects", "branch", "TEXT")
        self._ensure_column("projects", "monorepo_subpath", "TEXT")
        self._ensure_column("projects", "scope_components_json", "TEXT")
        self._ensure_column("memory_conflicts", "resolved_at", "REAL")
        self._ensure_column("provider_runs", "capabilities_json", "TEXT NOT NULL DEFAULT '{}'")
        self._ensure_column("provider_runs", "last_error", "TEXT")
        self._ensure_column("provider_runs", "updated_at", "REAL NOT NULL DEFAULT 0")
        self._ensure_column("provider_events", "session_id", "TEXT")
        self._ensure_column("provider_events", "payload_json", "TEXT NOT NULL DEFAULT '{}'")
        self._ensure_column("provider_events", "created_at", "REAL NOT NULL DEFAULT 0")
        self._ensure_column("maintenance_runs", "project_key", "TEXT")
        self._ensure_column("maintenance_runs", "result_json", "TEXT NOT NULL DEFAULT '{}'")
        self._ensure_column("maintenance_runs", "created_at", "REAL NOT NULL DEFAULT 0")
        self._ensure_column("project_aliases", "canonical_project_key", "TEXT")
        self._ensure_column("project_aliases", "created_at", "REAL NOT NULL DEFAULT 0")
        self._ensure_column("provider_configs", "config_json", "TEXT NOT NULL DEFAULT '{}'")
        self._ensure_column("provider_configs", "updated_at", "REAL NOT NULL DEFAULT 0")
        self._ensure_column("project_skills", "version", "INTEGER NOT NULL DEFAULT 1")
        self._ensure_column("project_skills", "last_used_at", "REAL")
        self._ensure_column("project_skills", "last_refreshed_at", "REAL")
        self._ensure_column("project_skills", "feedback_positive_count", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("project_skills", "feedback_negative_count", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("project_skills", "source_memory_count", "INTEGER NOT NULL DEFAULT 0")
        self._backfill_scope_columns()

    def _table_exists(self, table: str) -> bool:
        row = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        return row is not None

    def _table_columns(self, table: str) -> set[str]:
        if not self._table_exists(table):
            return set()
        rows = self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {row[1] for row in rows}

    def _execute_ddl(self, sql: str) -> None:
        try:
            self._conn.execute(sql)
        except sqlite3.OperationalError as exc:
            message = str(exc).lower()
            if "duplicate column name" in message or "already exists" in message:
                return
            raise

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        existing = self._table_columns(table)
        if column not in existing:
            self._execute_ddl(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _backfill_scope_columns(self) -> None:
        project_columns = self._table_columns("projects")
        if "canonical_project_key" in project_columns:
            self._conn.execute(
                """
                UPDATE projects
                SET canonical_project_key = COALESCE(NULLIF(TRIM(canonical_project_key), ''), project_key)
                WHERE project_key IS NOT NULL
                """
            )
        if "scope_components_json" in project_columns:
            self._conn.execute(
                """
                UPDATE projects
                SET scope_components_json = COALESCE(NULLIF(scope_components_json, ''), '[]')
                """
            )
        alias_columns = self._table_columns("project_aliases")
        if {"alias_key", "canonical_project_key"}.issubset(alias_columns):
            self._conn.execute(
                """
                DELETE FROM project_aliases
                WHERE canonical_project_key IS NULL OR TRIM(canonical_project_key) = ''
                """
            )

    def schema_version(self) -> int:
        row = self.fetchone("SELECT version FROM schema_version LIMIT 1")
        return int(row["version"]) if row else SCHEMA_VERSION

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        with self._lock:
            cursor = self._conn.execute(sql, params)
            self._conn.commit()
            return cursor

    def executemany(self, sql: str, seq: list[tuple[Any, ...]]) -> None:
        with self._lock:
            self._conn.executemany(sql, seq)
            self._conn.commit()

    def fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    def writable(self) -> bool:
        try:
            self.execute("CREATE TABLE IF NOT EXISTS _healthcheck (id INTEGER PRIMARY KEY, touched_at REAL)")
            self.execute("INSERT INTO _healthcheck(touched_at) VALUES (strftime('%s','now'))")
            return True
        except sqlite3.DatabaseError:
            return False

    def now(self) -> float:
        return time.time()

    async def async_execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        return await asyncio.to_thread(self.execute, sql, params)

    async def async_fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        return await asyncio.to_thread(self.fetchone, sql, params)

    async def async_fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        return await asyncio.to_thread(self.fetchall, sql, params)

    async def async_executemany(self, sql: str, seq: list[tuple[Any, ...]]) -> None:
        return await asyncio.to_thread(self.executemany, sql, seq)
