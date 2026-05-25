"""Project-level state and repository discovery."""

from __future__ import annotations

import os

import git_service
from storage import json_dumps, new_id, now_ts, Storage


def _display_name(path: str) -> str:
    clean = os.path.abspath(os.path.expanduser(path)).rstrip(os.sep)
    return os.path.basename(clean) or clean


class ProjectService:
    def __init__(self, storage: Storage):
        self.storage = storage

    def list_projects(self) -> list[dict]:
        return self.storage.query(
            """
            SELECT * FROM projects
            WHERE archived_at IS NULL
            ORDER BY updated_at DESC, name COLLATE NOCASE
            """
        )

    def get_project(self, project_id: str) -> dict | None:
        return self.storage.query_one(
            "SELECT * FROM projects WHERE id = ? AND archived_at IS NULL",
            (project_id,),
        )

    def create_project(self, path: str, name: str | None = None) -> dict:
        path = git_service.normalize_path(path)
        if not os.path.isdir(path):
            raise ValueError(f"Folder does not exist: {path}")
        existing = self.storage.query_one(
            "SELECT * FROM projects WHERE main_repo_path = ? AND archived_at IS NULL",
            (path,),
        )
        if existing:
            return existing

        repo = git_service.describe_repository(path)
        ts = now_ts()
        project = {
            "id": new_id("proj"),
            "name": name or repo.get("repo") or _display_name(path),
            "main_repo_path": path,
            "default_branch": repo.get("default_branch"),
            "github_owner": repo.get("owner"),
            "github_repo": repo.get("repo"),
            "repo_config_json": json_dumps({}),
            "created_at": ts,
            "updated_at": ts,
            "archived_at": None,
        }
        self.storage.execute(
            """
            INSERT INTO projects
            (id, name, main_repo_path, default_branch, github_owner, github_repo,
             repo_config_json, created_at, updated_at, archived_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project["id"], project["name"], project["main_repo_path"],
                project["default_branch"], project["github_owner"], project["github_repo"],
                project["repo_config_json"], project["created_at"], project["updated_at"],
                project["archived_at"],
            ),
        )
        self.storage.execute(
            """
            INSERT INTO repositories
            (id, project_id, path, remote_url, owner, name, default_branch, current_branch,
             last_status_json, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("repo"), project["id"], path, repo.get("remote_url"),
                repo.get("owner"), repo.get("repo"), repo.get("default_branch"),
                repo.get("current_branch"), None, ts,
            ),
        )
        return project

    def ensure_project_for_path(self, path: str) -> dict:
        return self.create_project(path)

    def archive_project(self, project_id: str) -> dict:
        ts = now_ts()
        self.storage.execute(
            "UPDATE projects SET archived_at = ?, updated_at = ? WHERE id = ?",
            (ts, ts, project_id),
        )
        return {"ok": True}
