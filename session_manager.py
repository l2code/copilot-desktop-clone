"""Session lifecycle and legacy conversation compatibility."""

from __future__ import annotations

import json
import os
import shutil
import time

from activity import ActivityLog
from project_service import ProjectService
from storage import json_dumps, json_loads, new_id, now_ts, Storage
from workspace_service import WorkspaceService


class SessionManager:
    def __init__(
        self,
        storage: Storage,
        projects: ProjectService,
        workspaces: WorkspaceService,
        activity: ActivityLog,
    ):
        self.storage = storage
        self.projects = projects
        self.workspaces = workspaces
        self.activity = activity

    def ensure_workspace_for_path(self, path: str) -> dict:
        project = self.projects.ensure_project_for_path(path)
        return self.workspaces.ensure_folder_workspace(project["id"], path)

    def create_session(
        self,
        workspace_id: str,
        session_type: str = "chat",
        title: str | None = None,
        *,
        session_id: str | None = None,
        created_at: float | None = None,
        updated_at: float | None = None,
        metadata: dict | None = None,
    ) -> dict:
        workspace = self.workspaces.get_workspace(workspace_id)
        if not workspace:
            raise ValueError("Workspace not found")
        ts = created_at or now_ts()
        row = {
            "id": session_id or new_id("ses"),
            "workspace_id": workspace_id,
            "title": title or "New chat",
            "session_type": session_type,
            "model": None,
            "reasoning": None,
            "mode": "interactive",
            "is_running": 0,
            "was_interrupted": 0,
            "interruption_reason": None,
            "forked_from_session_id": None,
            "metadata_json": json_dumps(metadata or {}),
            "created_at": ts,
            "updated_at": updated_at or ts,
            "archived_at": None,
        }
        self.storage.execute(
            """
            INSERT INTO sessions
            (id, workspace_id, title, session_type, model, reasoning, mode, is_running,
             was_interrupted, interruption_reason, forked_from_session_id, metadata_json,
             created_at, updated_at, archived_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"], row["workspace_id"], row["title"], row["session_type"],
                row["model"], row["reasoning"], row["mode"], row["is_running"],
                row["was_interrupted"], row["interruption_reason"],
                row["forked_from_session_id"], row["metadata_json"],
                row["created_at"], row["updated_at"], row["archived_at"],
            ),
        )
        self.activity.add(
            "chat",
            f"Created {session_type} session",
            row["title"],
            project_id=workspace["project_id"],
            workspace_id=workspace_id,
            session_id=row["id"],
            created_at=row["created_at"],
        )
        return row

    def get_session(self, session_id: str) -> dict | None:
        row = self.storage.query_one(
            "SELECT * FROM sessions WHERE id = ? AND archived_at IS NULL",
            (session_id,),
        )
        if row:
            row["metadata"] = json_loads(row.pop("metadata_json", None), {})
        return row

    def list_sessions(self, workspace_id: str) -> list[dict]:
        rows = self.storage.query(
            """
            SELECT * FROM sessions
            WHERE workspace_id = ? AND archived_at IS NULL
            ORDER BY updated_at DESC
            """,
            (workspace_id,),
        )
        for row in rows:
            row["metadata"] = json_loads(row.pop("metadata_json", None), {})
        return rows

    def replace_messages(self, session_id: str, messages: list[dict]) -> None:
        session = self.storage.query_one("SELECT * FROM sessions WHERE id = ?", (session_id,))
        if not session:
            raise ValueError("Session not found")
        self.storage.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        ts = now_ts()
        rows = []
        for idx, msg in enumerate(messages or []):
            role = str(msg.get("role") or "user")
            content = str(msg.get("content") or "")
            metadata = {}
            if msg.get("attachments"):
                metadata["attachments"] = msg.get("attachments")
            rows.append((
                new_id("msg"),
                session_id,
                role,
                content,
                json_dumps(metadata),
                ts + idx / 1000,
            ))
        if rows:
            self.storage.execute_many(
                """
                INSERT INTO messages(id, session_id, role, content, metadata_json, created_at)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        self.storage.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?",
            (ts, session_id),
        )

    def append_message(self, session_id: str, role: str, content: str, metadata: dict | None = None) -> dict:
        if not self.storage.query_one("SELECT id FROM sessions WHERE id = ?", (session_id,)):
            raise ValueError("Session not found")
        ts = now_ts()
        row = {
            "id": new_id("msg"),
            "session_id": session_id,
            "role": role,
            "content": content,
            "metadata_json": json_dumps(metadata or {}),
            "created_at": ts,
        }
        self.storage.execute(
            """
            INSERT INTO messages(id, session_id, role, content, metadata_json, created_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (row["id"], row["session_id"], row["role"], row["content"], row["metadata_json"], row["created_at"]),
        )
        self.storage.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?",
            (ts, session_id),
        )
        row["metadata"] = metadata or {}
        row.pop("metadata_json", None)
        return row

    def set_running(self, session_id: str, running: bool, interrupted: bool = False, reason: str | None = None) -> None:
        self.storage.execute(
            """
            UPDATE sessions
            SET is_running = ?, was_interrupted = ?, interruption_reason = ?, updated_at = ?
            WHERE id = ?
            """,
            (1 if running else 0, 1 if interrupted else 0, reason, now_ts(), session_id),
        )

    def get_messages(self, session_id: str) -> list[dict]:
        rows = self.storage.query(
            """
            SELECT role, content, metadata_json
            FROM messages
            WHERE session_id = ?
            ORDER BY created_at ASC
            """,
            (session_id,),
        )
        out = []
        for row in rows:
            item = {"role": row["role"], "content": row["content"]}
            metadata = json_loads(row.get("metadata_json"), {})
            if metadata.get("attachments"):
                item["attachments"] = metadata["attachments"]
            out.append(item)
        return out

    def list_conversations(self) -> list[dict]:
        rows = self.storage.query(
            """
            SELECT s.id, s.title, s.updated_at AS updated, w.path AS cwd,
                   w.id AS workspace_id, p.id AS project_id, p.name AS project_name
            FROM sessions s
            JOIN workspaces w ON w.id = s.workspace_id
            JOIN projects p ON p.id = w.project_id
            WHERE s.archived_at IS NULL AND w.archived_at IS NULL AND p.archived_at IS NULL
            ORDER BY s.updated_at DESC
            """
        )
        return rows

    def get_conversation(self, conv_id: str) -> dict | None:
        row = self.storage.query_one(
            """
            SELECT s.*, w.path AS cwd, w.id AS workspace_id, p.id AS project_id
            FROM sessions s
            JOIN workspaces w ON w.id = s.workspace_id
            JOIN projects p ON p.id = w.project_id
            WHERE s.id = ? AND s.archived_at IS NULL
            """,
            (conv_id,),
        )
        if not row:
            return None
        return {
            "id": row["id"],
            "title": row["title"],
            "created": row["created_at"],
            "updated": row["updated_at"],
            "cwd": row["cwd"],
            "workspace_id": row["workspace_id"],
            "project_id": row["project_id"],
            "messages": self.get_messages(conv_id),
        }

    def save_conversation(self, conv_id: str, title: str, messages: list[dict], cwd: str) -> dict:
        existing = self.storage.query_one(
            "SELECT * FROM sessions WHERE id = ? AND archived_at IS NULL",
            (conv_id,),
        )
        if existing:
            session_id = existing["id"]
            workspace = self.workspaces.get_workspace(existing["workspace_id"])
        else:
            workspace = self.ensure_workspace_for_path(cwd)
            session = self.create_session(
                workspace["id"],
                "chat",
                title or "New chat",
                session_id=conv_id,
            )
            session_id = session["id"]

        ts = now_ts()
        self.storage.execute(
            "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
            (title or "New chat", ts, session_id),
        )
        self.replace_messages(session_id, messages)
        if workspace:
            self.activity.add(
                "chat",
                "Saved conversation",
                title or "New chat",
                project_id=workspace["project_id"],
                workspace_id=workspace["id"],
                session_id=session_id,
            )
        return {"ok": True, "session_id": session_id}

    def archive_session(self, session_id: str) -> dict:
        session = self.storage.query_one("SELECT * FROM sessions WHERE id = ?", (session_id,))
        ts = now_ts()
        self.storage.execute(
            "UPDATE sessions SET archived_at = ?, updated_at = ? WHERE id = ?",
            (ts, ts, session_id),
        )
        if session:
            workspace = self.workspaces.get_workspace(session["workspace_id"])
            self.activity.add(
                "chat",
                "Archived session",
                session.get("title"),
                project_id=workspace["project_id"] if workspace else None,
                workspace_id=session["workspace_id"],
                session_id=session_id,
            )
        return {"ok": True}

    def migrate_legacy_history(self, history_file: str, default_path: str) -> dict:
        if self.storage.get_setting("legacy_history_migrated") == "1":
            return {"ok": True, "migrated": 0}
        migrated = 0
        try:
            with open(history_file, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            self.storage.set_setting("legacy_history_migrated", "1")
            return {"ok": True, "migrated": 0}
        conversations = data.get("conversations") if isinstance(data, dict) else None
        if not isinstance(conversations, list):
            self.storage.set_setting("legacy_history_migrated", "1")
            return {"ok": True, "migrated": 0}

        if os.path.exists(history_file):
            stamp = time.strftime("%Y%m%d-%H%M%S")
            backup = f"{history_file}.bak-{stamp}"
            if not os.path.exists(backup):
                try:
                    shutil.copy2(history_file, backup)
                except Exception:
                    pass

        for conv in conversations:
            if not isinstance(conv, dict):
                continue
            conv_id = conv.get("id") or new_id("ses")
            if self.storage.query_one("SELECT id FROM sessions WHERE id = ?", (conv_id,)):
                continue
            cwd = conv.get("cwd") or default_path
            try:
                workspace = self.ensure_workspace_for_path(cwd)
            except Exception:
                workspace = self.ensure_workspace_for_path(default_path)
            title = conv.get("title") or "Imported chat"
            created = float(conv.get("created") or conv.get("updated") or now_ts())
            updated = float(conv.get("updated") or created)
            self.create_session(
                workspace["id"],
                "chat",
                title,
                session_id=conv_id,
                created_at=created,
                updated_at=updated,
                metadata={"legacy": "history.json"},
            )
            self.replace_messages(conv_id, conv.get("messages") or [])
            self.storage.execute(
                "UPDATE sessions SET created_at = ?, updated_at = ? WHERE id = ?",
                (created, updated, conv_id),
            )
            migrated += 1
        self.storage.set_setting("legacy_history_migrated", "1")
        return {"ok": True, "migrated": migrated}
