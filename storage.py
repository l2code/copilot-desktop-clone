"""
SQLite storage for Copilot Desktop.

The app started with a small JSON history file. This module is the first step
toward the GitHub Copilot App-style model: projects own workspaces, workspaces
own sessions, and durable activity records explain what happened.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
import shutil
import sqlite3
import time
import uuid


APP_DIR = os.environ.get("COPILOT_DESKTOP_HOME") or os.path.join(os.path.expanduser("~"), ".copilot-desktop")
DB_FILE = os.path.join(APP_DIR, "data.db")
SCHEMA_VERSION = 1


def now_ts() -> float:
    return time.time()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def json_dumps(value) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def json_loads(value, default=None):
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def row_to_dict(row) -> dict | None:
    return dict(row) if row is not None else None


class Storage:
    def __init__(self, db_path: str = DB_FILE):
        self.db_path = db_path
        self._memory_conn: sqlite3.Connection | None = None
        self.initialize()

    @contextmanager
    def connection(self):
        if self.db_path == ":memory:":
            if self._memory_conn is None:
                self._memory_conn = sqlite3.connect(self.db_path)
                self._memory_conn.row_factory = sqlite3.Row
                self._memory_conn.execute("PRAGMA foreign_keys = ON")
            conn = self._memory_conn
            yield conn
            return

        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def initialize(self) -> None:
        if self.db_path != ":memory:":
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with self.connection() as conn:
            current = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if current and current < SCHEMA_VERSION and self.db_path != ":memory:":
                stamp = time.strftime("%Y%m%d-%H%M%S")
                shutil.copy2(self.db_path, f"{self.db_path}.bak-{stamp}")
            self._apply_schema(conn)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.commit()

    def _apply_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                main_repo_path TEXT NOT NULL,
                default_branch TEXT,
                github_owner TEXT,
                github_repo TEXT,
                repo_config_json TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                archived_at REAL
            );
            CREATE INDEX IF NOT EXISTS idx_projects_path ON projects(main_repo_path);

            CREATE TABLE IF NOT EXISTS worktrees (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                path TEXT NOT NULL,
                branch TEXT,
                base_branch TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                archived_at REAL,
                FOREIGN KEY(project_id) REFERENCES projects(id)
            );
            CREATE INDEX IF NOT EXISTS idx_worktrees_project ON worktrees(project_id);

            CREATE TABLE IF NOT EXISTS workspaces (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                worktree_id TEXT,
                name TEXT NOT NULL,
                path TEXT NOT NULL,
                workspace_type TEXT NOT NULL,
                branch TEXT,
                base_branch TEXT,
                source_issue_number INTEGER,
                source_pr_number INTEGER,
                metadata_json TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                archived_at REAL,
                FOREIGN KEY(project_id) REFERENCES projects(id),
                FOREIGN KEY(worktree_id) REFERENCES worktrees(id)
            );
            CREATE INDEX IF NOT EXISTS idx_workspaces_project ON workspaces(project_id);
            CREATE INDEX IF NOT EXISTS idx_workspaces_path ON workspaces(path);

            CREATE TABLE IF NOT EXISTS workspace_repo_contexts (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                owner TEXT,
                repo TEXT,
                remote_url TEXT,
                default_branch TEXT,
                current_branch TEXT,
                metadata_json TEXT,
                updated_at REAL NOT NULL,
                FOREIGN KEY(workspace_id) REFERENCES workspaces(id)
            );

            CREATE TABLE IF NOT EXISTS workspace_checkout_bindings (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                path TEXT NOT NULL,
                branch TEXT,
                head_sha TEXT,
                updated_at REAL NOT NULL,
                FOREIGN KEY(workspace_id) REFERENCES workspaces(id)
            );

            CREATE TABLE IF NOT EXISTS workspace_diff_snapshots (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                branch TEXT,
                head_sha TEXT,
                status_json TEXT,
                diff_stat_json TEXT,
                created_at REAL NOT NULL,
                FOREIGN KEY(workspace_id) REFERENCES workspaces(id)
            );

            CREATE TABLE IF NOT EXISTS workspace_pr_sync_status (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                pull_request_id TEXT,
                state TEXT,
                metadata_json TEXT,
                updated_at REAL NOT NULL,
                FOREIGN KEY(workspace_id) REFERENCES workspaces(id)
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                title TEXT NOT NULL,
                session_type TEXT NOT NULL,
                model TEXT,
                reasoning TEXT,
                mode TEXT,
                is_running INTEGER NOT NULL DEFAULT 0,
                was_interrupted INTEGER NOT NULL DEFAULT 0,
                interruption_reason TEXT,
                forked_from_session_id TEXT,
                metadata_json TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                archived_at REAL,
                FOREIGN KEY(workspace_id) REFERENCES workspaces(id)
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_workspace ON sessions(workspace_id);
            CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at);

            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata_json TEXT,
                created_at REAL NOT NULL,
                FOREIGN KEY(session_id) REFERENCES sessions(id)
            );
            CREATE INDEX IF NOT EXISTS idx_messages_session_created ON messages(session_id, created_at);

            CREATE TABLE IF NOT EXISTS session_forks (
                id TEXT PRIMARY KEY,
                source_session_id TEXT NOT NULL,
                forked_session_id TEXT NOT NULL,
                from_message_id TEXT,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS session_automations (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                name TEXT NOT NULL,
                prompt TEXT NOT NULL,
                schedule_json TEXT,
                status TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                archived_at REAL,
                FOREIGN KEY(session_id) REFERENCES sessions(id)
            );

            CREATE TABLE IF NOT EXISTS activity_items (
                id TEXT PRIMARY KEY,
                project_id TEXT,
                workspace_id TEXT,
                session_id TEXT,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT,
                metadata_json TEXT,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_activity_workspace ON activity_items(workspace_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_activity_project ON activity_items(project_id, created_at);

            CREATE TABLE IF NOT EXISTS github_accounts (
                id TEXT PRIMARY KEY,
                login TEXT NOT NULL,
                avatar_url TEXT,
                token_source TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS repositories (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                path TEXT NOT NULL,
                remote_url TEXT,
                owner TEXT,
                name TEXT,
                default_branch TEXT,
                current_branch TEXT,
                last_status_json TEXT,
                updated_at REAL NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(id)
            );

            CREATE TABLE IF NOT EXISTS issues (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                number INTEGER NOT NULL,
                title TEXT NOT NULL,
                state TEXT NOT NULL,
                author TEXT,
                url TEXT,
                metadata_json TEXT,
                updated_at REAL NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(id)
            );

            CREATE TABLE IF NOT EXISTS pull_requests (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                number INTEGER NOT NULL,
                title TEXT NOT NULL,
                state TEXT NOT NULL,
                author TEXT,
                head_branch TEXT,
                base_branch TEXT,
                url TEXT,
                metadata_json TEXT,
                updated_at REAL NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(id)
            );

            CREATE TABLE IF NOT EXISTS review_threads (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                pull_request_id TEXT,
                path TEXT,
                line INTEGER,
                state TEXT NOT NULL,
                metadata_json TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(id)
            );

            CREATE TABLE IF NOT EXISTS review_thread_messages (
                id TEXT PRIMARY KEY,
                review_thread_id TEXT NOT NULL,
                author TEXT,
                body TEXT NOT NULL,
                metadata_json TEXT,
                created_at REAL NOT NULL,
                FOREIGN KEY(review_thread_id) REFERENCES review_threads(id)
            );

            CREATE TABLE IF NOT EXISTS inline_review_comments (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                pull_request_id TEXT,
                path TEXT,
                line INTEGER,
                body TEXT NOT NULL,
                metadata_json TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(id)
            );

            CREATE TABLE IF NOT EXISTS inline_review_replies (
                id TEXT PRIMARY KEY,
                comment_id TEXT NOT NULL,
                body TEXT NOT NULL,
                metadata_json TEXT,
                created_at REAL NOT NULL,
                FOREIGN KEY(comment_id) REFERENCES inline_review_comments(id)
            );

            CREATE TABLE IF NOT EXISTS workflows (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                name TEXT NOT NULL,
                definition_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                archived_at REAL,
                FOREIGN KEY(project_id) REFERENCES projects(id)
            );

            CREATE TABLE IF NOT EXISTS workflow_runs (
                id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                workspace_id TEXT,
                status TEXT NOT NULL,
                output TEXT,
                metadata_json TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY(workflow_id) REFERENCES workflows(id),
                FOREIGN KEY(workspace_id) REFERENCES workspaces(id)
            );

            CREATE TABLE IF NOT EXISTS attachments (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                message_id TEXT,
                kind TEXT NOT NULL,
                name TEXT,
                path TEXT,
                metadata_json TEXT,
                created_at REAL NOT NULL,
                FOREIGN KEY(session_id) REFERENCES sessions(id),
                FOREIGN KEY(message_id) REFERENCES messages(id)
            );
            """
        )

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        with self.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def query_one(self, sql: str, params: tuple = ()) -> dict | None:
        with self.connection() as conn:
            row = conn.execute(sql, params).fetchone()
        return row_to_dict(row)

    def execute(self, sql: str, params: tuple = ()) -> None:
        with self.connection() as conn:
            conn.execute(sql, params)
            conn.commit()

    def execute_many(self, sql: str, rows: list[tuple]) -> None:
        with self.connection() as conn:
            conn.executemany(sql, rows)
            conn.commit()

    def get_setting(self, key: str, default=None):
        row = self.query_one("SELECT value FROM settings WHERE key = ?", (key,))
        if not row:
            return default
        return row["value"]

    def set_setting(self, key: str, value) -> None:
        self.execute(
            """
            INSERT INTO settings(key, value, updated_at)
            VALUES(?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (key, str(value), now_ts()),
        )
