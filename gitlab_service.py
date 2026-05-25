"""GitLab REST integration for backlog, stories, epics, and issue creation."""

from __future__ import annotations

import json
import os
from urllib.parse import quote, urlencode, urlparse
import urllib.error
import urllib.request


class GitLabService:
    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or os.environ.get("GITLAB_URL") or "https://gitlab.com").rstrip("/")

    @property
    def host(self) -> str:
        return urlparse(self.base_url).netloc or "gitlab.com"

    def discover_token(self) -> tuple[str | None, str | None]:
        for name in ("GITLAB_TOKEN", "GITLAB_PERSONAL_ACCESS_TOKEN", "GL_TOKEN", "GITLAB_PRIVATE_TOKEN"):
            if os.environ.get(name):
                return os.environ[name], name
        return None, None

    def env_status(self) -> dict:
        token, source = self.discover_token()
        return {
            "ok": True,
            "base_url": self.base_url,
            "host": self.host,
            "token_source": source,
            "has_token": bool(token),
            "default_project": os.environ.get("GITLAB_PROJECT_ID") or os.environ.get("GITLAB_PROJECT_PATH"),
            "default_group": os.environ.get("GITLAB_GROUP_ID") or os.environ.get("GITLAB_GROUP_PATH"),
        }

    def auth_status(self) -> dict:
        token, source = self.discover_token()
        if not token:
            return {"ok": True, "authenticated": False, "base_url": self.base_url, "source": None}
        res = self._request("GET", "/user")
        if not res.get("ok"):
            return {
                "ok": True,
                "authenticated": False,
                "base_url": self.base_url,
                "source": source,
                "error": res.get("error"),
            }
        user = res.get("data") or {}
        return {
            "ok": True,
            "authenticated": True,
            "base_url": self.base_url,
            "source": source,
            "username": user.get("username"),
            "name": user.get("name"),
            "avatar_url": user.get("avatar_url"),
        }

    def _request(self, method: str, path: str, body: dict | None = None, params: dict | None = None) -> dict:
        token, _ = self.discover_token()
        if not token:
            return {"ok": False, "error": "GitLab authentication is not configured."}
        qs = ("?" + urlencode({k: v for k, v in (params or {}).items() if v not in (None, "")}, doseq=True)) if params else ""
        data = None
        headers = {
            "Accept": "application/json",
            "User-Agent": "copilot-desktop-clone",
            "PRIVATE-TOKEN": token,
        }
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(f"{self.base_url}/api/v4{path}{qs}", data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                return {"ok": True, "data": json.loads(raw) if raw else None}
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw)
                raw = parsed.get("message") or parsed.get("error") or raw
            except Exception:
                pass
            return {"ok": False, "status": e.code, "error": raw}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @staticmethod
    def _project(value: str | int) -> str:
        return quote(str(value), safe="")

    @staticmethod
    def _group(value: str | int) -> str:
        return quote(str(value), safe="")

    def get_project(self, project: str | int) -> dict:
        return self._request("GET", f"/projects/{self._project(project)}")

    def list_project_issues(
        self,
        project: str | int,
        *,
        state: str = "opened",
        labels: str | None = None,
        search: str | None = None,
        page: int = 1,
        per_page: int = 50,
    ) -> dict:
        res = self._request("GET", f"/projects/{self._project(project)}/issues", params={
            "state": state,
            "labels": labels,
            "search": search,
            "scope": "all",
            "order_by": "updated_at",
            "sort": "desc",
            "page": page,
            "per_page": min(max(int(per_page or 50), 1), 100),
        })
        if res.get("ok"):
            res["issues"] = [self._issue(i) for i in (res.get("data") or [])]
        return res

    def list_group_issues(
        self,
        group: str | int,
        *,
        state: str = "opened",
        labels: str | None = None,
        search: str | None = None,
        page: int = 1,
        per_page: int = 50,
    ) -> dict:
        res = self._request("GET", f"/groups/{self._group(group)}/issues", params={
            "state": state,
            "labels": labels,
            "search": search,
            "scope": "all",
            "order_by": "updated_at",
            "sort": "desc",
            "page": page,
            "per_page": min(max(int(per_page or 50), 1), 100),
        })
        if res.get("ok"):
            res["issues"] = [self._issue(i) for i in (res.get("data") or [])]
        return res

    def create_issue(self, project: str | int, title: str, description: str = "", labels: str | None = None) -> dict:
        body = {"title": title, "description": description or ""}
        if labels:
            body["labels"] = labels
        res = self._request("POST", f"/projects/{self._project(project)}/issues", body=body)
        if res.get("ok"):
            res["issue"] = self._issue(res.get("data") or {})
        return res

    def update_issue(self, project: str | int, issue_iid: int, patch: dict) -> dict:
        allowed = {
            "title", "description", "state_event", "labels", "add_labels", "remove_labels",
            "assignee_ids", "milestone_id", "due_date", "weight", "confidential",
            "discussion_locked", "issue_type",
        }
        body = {k: v for k, v in (patch or {}).items() if k in allowed}
        res = self._request("PUT", f"/projects/{self._project(project)}/issues/{int(issue_iid)}", body=body)
        if res.get("ok"):
            res["issue"] = self._issue(res.get("data") or {})
        return res

    def create_issue_note(self, project: str | int, issue_iid: int, body: str) -> dict:
        return self._request("POST", f"/projects/{self._project(project)}/issues/{int(issue_iid)}/notes", body={"body": body})

    def list_group_epics(self, group: str | int, state: str = "opened") -> dict:
        res = self._request("GET", f"/groups/{self._group(group)}/epics", params={
            "state": state,
            "order_by": "updated_at",
            "sort": "desc",
            "per_page": 50,
        })
        if res.get("ok"):
            res["epics"] = [self._epic(e) for e in (res.get("data") or [])]
            res["deprecated"] = True
        return res

    def create_group_epic(self, group: str | int, title: str, description: str = "", labels: str | None = None) -> dict:
        body = {"title": title, "description": description or ""}
        if labels:
            body["labels"] = labels
        res = self._request("POST", f"/groups/{self._group(group)}/epics", body=body)
        if res.get("ok"):
            res["epic"] = self._epic(res.get("data") or {})
            res["deprecated"] = True
        return res

    def update_group_epic(self, group: str | int, epic_iid: int, patch: dict) -> dict:
        allowed = {"title", "description", "state_event", "labels", "add_labels", "remove_labels", "due_date_fixed", "start_date_fixed"}
        body = {k: v for k, v in (patch or {}).items() if k in allowed}
        res = self._request("PUT", f"/groups/{self._group(group)}/epics/{int(epic_iid)}", body=body)
        if res.get("ok"):
            res["epic"] = self._epic(res.get("data") or {})
            res["deprecated"] = True
        return res

    @staticmethod
    def _issue(issue: dict) -> dict:
        return {
            "id": issue.get("id"),
            "iid": issue.get("iid"),
            "project_id": issue.get("project_id"),
            "title": issue.get("title"),
            "description": issue.get("description"),
            "state": issue.get("state"),
            "labels": issue.get("labels") or [],
            "assignees": [a.get("username") for a in issue.get("assignees") or []],
            "author": (issue.get("author") or {}).get("username"),
            "web_url": issue.get("web_url"),
            "updated_at": issue.get("updated_at"),
            "created_at": issue.get("created_at"),
            "weight": issue.get("weight"),
            "due_date": issue.get("due_date"),
            "issue_type": issue.get("issue_type"),
            "references": issue.get("references") or {},
        }

    @staticmethod
    def _epic(epic: dict) -> dict:
        return {
            "id": epic.get("id"),
            "iid": epic.get("iid"),
            "title": epic.get("title"),
            "description": epic.get("description"),
            "state": epic.get("state"),
            "labels": epic.get("labels") or [],
            "author": (epic.get("author") or {}).get("username"),
            "web_url": epic.get("web_url"),
            "updated_at": epic.get("updated_at"),
            "created_at": epic.get("created_at"),
        }
