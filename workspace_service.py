"""Workspace and worktree records."""

from __future__ import annotations

import os

import git_service
from storage import json_dumps, new_id, now_ts, Storage


def _workspace_name(path: str, branch: str | None = None) -> str:
    base = os.path.basename(os.path.abspath(os.path.expanduser(path)).rstrip(os.sep)) or path
    return f"{base} · {branch}" if branch else base


class WorkspaceService:
    def __init__(self, storage: Storage):
        self.storage = storage

    def list_workspaces(self, project_id: str) -> list[dict]:
        return self.storage.query(
            """
            SELECT * FROM workspaces
            WHERE project_id = ? AND archived_at IS NULL
            ORDER BY updated_at DESC, name COLLATE NOCASE
            """,
            (project_id,),
        )

    def get_workspace(self, workspace_id: str) -> dict | None:
        return self.storage.query_one(
            "SELECT * FROM workspaces WHERE id = ? AND archived_at IS NULL",
            (workspace_id,),
        )

    def ensure_folder_workspace(self, project_id: str, path: str) -> dict:
        path = git_service.normalize_path(path)
        existing = self.storage.query_one(
            """
            SELECT * FROM workspaces
            WHERE project_id = ? AND path = ? AND workspace_type = 'folder' AND archived_at IS NULL
            """,
            (project_id, path),
        )
        if existing:
            return existing
        return self.create_workspace(project_id, "folder", path=path)

    def create_workspace(
        self,
        project_id: str,
        workspace_type: str,
        *,
        path: str,
        name: str | None = None,
        branch: str | None = None,
        base_branch: str | None = None,
        source_issue_number: int | None = None,
        source_pr_number: int | None = None,
        metadata: dict | None = None,
    ) -> dict:
        path = git_service.normalize_path(path)
        if not os.path.isdir(path):
            raise ValueError(f"Workspace folder does not exist: {path}")
        if branch is None:
            branch = git_service.get_current_branch(path)
        ts = now_ts()
        row = {
            "id": new_id("ws"),
            "project_id": project_id,
            "worktree_id": None,
            "name": name or _workspace_name(path, branch),
            "path": path,
            "workspace_type": workspace_type,
            "branch": branch,
            "base_branch": base_branch,
            "source_issue_number": source_issue_number,
            "source_pr_number": source_pr_number,
            "metadata_json": json_dumps(metadata or {}),
            "created_at": ts,
            "updated_at": ts,
            "archived_at": None,
        }
        self.storage.execute(
            """
            INSERT INTO workspaces
            (id, project_id, worktree_id, name, path, workspace_type, branch, base_branch,
             source_issue_number, source_pr_number, metadata_json, created_at, updated_at, archived_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"], row["project_id"], row["worktree_id"], row["name"],
                row["path"], row["workspace_type"], row["branch"], row["base_branch"],
                row["source_issue_number"], row["source_pr_number"], row["metadata_json"],
                row["created_at"], row["updated_at"], row["archived_at"],
            ),
        )
        return row

    def archive_workspace(self, workspace_id: str) -> dict:
        ts = now_ts()
        self.storage.execute(
            "UPDATE workspaces SET archived_at = ?, updated_at = ? WHERE id = ?",
            (ts, ts, workspace_id),
        )
        return {"ok": True}
