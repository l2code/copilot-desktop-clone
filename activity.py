"""Durable activity feed for projects, workspaces, and sessions."""

from __future__ import annotations

from storage import json_dumps, json_loads, new_id, now_ts, Storage


class ActivityLog:
    def __init__(self, storage: Storage):
        self.storage = storage

    def add(
        self,
        kind: str,
        title: str,
        body: str | None = None,
        *,
        project_id: str | None = None,
        workspace_id: str | None = None,
        session_id: str | None = None,
        metadata: dict | None = None,
        created_at: float | None = None,
    ) -> dict:
        item = {
            "id": new_id("act"),
            "project_id": project_id,
            "workspace_id": workspace_id,
            "session_id": session_id,
            "kind": kind,
            "title": title,
            "body": body,
            "metadata_json": json_dumps(metadata),
            "created_at": created_at or now_ts(),
        }
        self.storage.execute(
            """
            INSERT INTO activity_items
            (id, project_id, workspace_id, session_id, kind, title, body, metadata_json, created_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            tuple(item[k] for k in (
                "id", "project_id", "workspace_id", "session_id", "kind",
                "title", "body", "metadata_json", "created_at"
            )),
        )
        item["metadata"] = metadata or {}
        item.pop("metadata_json", None)
        return item

    def list_workspace(self, workspace_id: str, limit: int = 100) -> list[dict]:
        rows = self.storage.query(
            """
            SELECT * FROM activity_items
            WHERE workspace_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (workspace_id, limit),
        )
        return [self._inflate(r) for r in rows]

    def list_project(self, project_id: str, limit: int = 100) -> list[dict]:
        rows = self.storage.query(
            """
            SELECT * FROM activity_items
            WHERE project_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (project_id, limit),
        )
        return [self._inflate(r) for r in rows]

    @staticmethod
    def _inflate(row: dict) -> dict:
        row = dict(row)
        row["metadata"] = json_loads(row.pop("metadata_json", None), {})
        return row
