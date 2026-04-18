from __future__ import annotations

import sqlite3
from pathlib import Path

from memory_agent_tool.database import Database


def _create_legacy_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE schema_version (
            version INTEGER NOT NULL
        );
        INSERT INTO schema_version(version) VALUES (1);

        CREATE TABLE projects (
            project_key TEXT PRIMARY KEY,
            display_name TEXT,
            status TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE TABLE memory_conflicts (
            conflict_id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_key TEXT NOT NULL,
            existing_memory_id INTEGER NOT NULL,
            candidate_memory_id INTEGER NOT NULL,
            resolution TEXT NOT NULL,
            reason TEXT NOT NULL,
            created_at REAL NOT NULL
        );

        CREATE TABLE project_skills (
            skill_id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_key TEXT NOT NULL,
            name TEXT NOT NULL,
            content TEXT NOT NULL,
            file_path TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()


def _column_names(db: Database, table: str) -> set[str]:
    return {row["name"] for row in db.fetchall(f"PRAGMA table_info({table})")}


def test_database_initializes_new_schema_idempotently(tmp_path: Path):
    db_path = tmp_path / "state.db"
    first = Database(db_path)
    second = Database(db_path)

    assert "canonical_project_key" in _column_names(first, "projects")
    assert "resolved_at" in _column_names(first, "memory_conflicts")
    assert {
        "version",
        "last_used_at",
        "last_refreshed_at",
        "feedback_positive_count",
        "feedback_negative_count",
        "source_memory_count",
    }.issubset(_column_names(first, "project_skills"))
    assert "project_aliases" in {
        row["name"] for row in first.fetchall("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert second.schema_version() >= first.schema_version()

    first.close()
    second.close()


def test_database_upgrades_legacy_schema_without_duplicate_column_errors(tmp_path: Path):
    db_path = tmp_path / "legacy.db"
    _create_legacy_db(db_path)

    db = Database(db_path)

    project_columns = _column_names(db, "projects")
    assert {
        "canonical_project_key",
        "workspace",
        "branch",
        "monorepo_subpath",
        "scope_components_json",
    }.issubset(project_columns)
    assert "resolved_at" in _column_names(db, "memory_conflicts")
    assert {
        "version",
        "last_used_at",
        "last_refreshed_at",
        "feedback_positive_count",
        "feedback_negative_count",
        "source_memory_count",
    }.issubset(_column_names(db, "project_skills"))

    db.close()


def test_database_can_restart_after_partial_upgrade(tmp_path: Path):
    db_path = tmp_path / "partial.db"
    _create_legacy_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("ALTER TABLE projects ADD COLUMN canonical_project_key TEXT")
    conn.execute("ALTER TABLE project_skills ADD COLUMN version INTEGER NOT NULL DEFAULT 1")
    conn.commit()
    conn.close()

    first = Database(db_path)
    second = Database(db_path)

    row = second.fetchone(
        """
        SELECT canonical_project_key
        FROM projects
        LIMIT 1
        """
    )
    assert row is None or row["canonical_project_key"] is None
    assert "last_refreshed_at" in _column_names(second, "project_skills")

    first.close()
    second.close()


def test_duplicate_column_race_is_ignored_when_column_appears_during_alter(tmp_path: Path):
    db_path = tmp_path / "race.db"
    _create_legacy_db(db_path)
    db = Database(db_path)
    db._execute_ddl("ALTER TABLE projects ADD COLUMN canonical_project_key TEXT")
    db._execute_ddl("ALTER TABLE projects ADD COLUMN canonical_project_key TEXT")
    assert "canonical_project_key" in _column_names(db, "projects")

    db.close()
