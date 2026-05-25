"""GitLab REST integration for backlog, stories, epics, and issue creation."""

from __future__ import annotations

import json
import os
import re
from urllib.parse import quote, urlencode, urlparse
import urllib.error
import urllib.request


class GitLabService:
    def __init__(self, base_url: str | None = None, settings_getter=None):
        self._base_url = base_url
        self._settings_getter = settings_getter

    def _settings(self) -> dict:
        if not self._settings_getter:
            return {}
        try:
            data = self._settings_getter() or {}
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _normalize_base_url(value: str | None) -> str | None:
        if not value:
            return None
        url = value.strip().rstrip("/")
        if not url:
            return None
        if not urlparse(url).scheme:
            url = "https://" + url
        parsed = urlparse(url)
        path = parsed.path.rstrip("/")
        if path.endswith("/api/v4"):
            path = path[:-len("/api/v4")]
            url = parsed._replace(path=path or "", params="", query="", fragment="").geturl()
        return url.rstrip("/")

    def discover_base_url(self) -> tuple[str, str]:
        base = self._normalize_base_url(self._base_url)
        if base:
            return base, "constructor"
        settings_url = self._normalize_base_url(self._settings().get("gitlab_url"))
        if settings_url:
            return settings_url, "app settings"
        for name in ("GITLAB_URL", "GITLAB_BASE_URL", "CI_SERVER_URL", "GITLAB_API_URL"):
            base = self._normalize_base_url(os.environ.get(name))
            if base:
                return base, name
        return "https://gitlab.com", "default"

    @property
    def base_url(self) -> str:
        base, _ = self.discover_base_url()
        return base

    @property
    def host(self) -> str:
        return urlparse(self.base_url).netloc or "gitlab.com"

    def discover_token(self) -> tuple[str | None, str | None]:
        settings_token = str(self._settings().get("gitlab_token") or "").strip()
        if settings_token:
            return settings_token, "app settings"
        for name in ("GITLAB_TOKEN", "GITLAB_PERSONAL_ACCESS_TOKEN", "GL_TOKEN", "GITLAB_PRIVATE_TOKEN"):
            if os.environ.get(name):
                return os.environ[name], name
        return None, None

    def default_project(self) -> str | None:
        settings = self._settings()
        return (
            str(settings.get("gitlab_project") or "").strip()
            or os.environ.get("GITLAB_PROJECT_ID")
            or os.environ.get("GITLAB_PROJECT_PATH")
            or None
        )

    def default_group(self) -> str | None:
        settings = self._settings()
        return (
            str(settings.get("gitlab_group") or "").strip()
            or os.environ.get("GITLAB_GROUP_ID")
            or os.environ.get("GITLAB_GROUP_PATH")
            or None
        )

    def env_status(self) -> dict:
        token, source = self.discover_token()
        base_url, url_source = self.discover_base_url()
        return {
            "ok": True,
            "base_url": base_url,
            "api_url": self.api_url,
            "host": urlparse(base_url).netloc or "gitlab.com",
            "url_source": url_source,
            "token_source": source,
            "auth_type": self.auth_type(),
            "has_token": bool(token),
            "default_project": self.default_project(),
            "default_group": self.default_group(),
        }

    @property
    def api_url(self) -> str:
        return f"{self.base_url}/api/v4"

    def auth_type(self) -> str:
        settings_type = str(self._settings().get("gitlab_auth_type") or "").strip().lower()
        env_type = str(os.environ.get("GITLAB_AUTH_TYPE") or os.environ.get("GITLAB_TOKEN_AUTH") or "").strip().lower()
        value = settings_type or env_type or "private-token"
        if value in ("authorization", "oauth", "oauth-bearer"):
            value = "bearer"
        if value not in ("private-token", "bearer", "both"):
            value = "private-token"
        return value

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
        base_url = self.base_url
        qs = ("?" + urlencode({k: v for k, v in (params or {}).items() if v not in (None, "")}, doseq=True)) if params else ""
        data = None
        headers = {
            "Accept": "application/json",
            "User-Agent": "copilot-desktop-clone",
        }
        auth_type = self.auth_type()
        if auth_type in ("private-token", "both"):
            headers["PRIVATE-TOKEN"] = token
        if auth_type in ("bearer", "both"):
            headers["Authorization"] = f"Bearer {token}"
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(f"{base_url}/api/v4{path}{qs}", data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                try:
                    return {"ok": True, "data": json.loads(raw) if raw else None}
                except Exception:
                    return {"ok": False, "status": getattr(resp, "status", None), "error": self._format_non_json_response(raw, getattr(resp, "url", ""))}
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw)
                raw = parsed.get("message") or parsed.get("error") or raw
            except Exception:
                raw = self._format_non_json_response(raw, getattr(e, "url", ""))
            return {"ok": False, "status": e.code, "error": raw}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @staticmethod
    def _format_non_json_response(raw: str, url: str = "") -> str:
        text = raw or ""
        if "<html" in text.lower() or "<!doctype html" in text.lower():
            title = ""
            match = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.I | re.S)
            if match:
                title = re.sub(r"\s+", " ", match.group(1)).strip()
            hints = "GitLab API returned HTML instead of JSON"
            if title:
                hints += f" ({title})"
            hints += ". This usually means the URL is an SSO/proxy/front-door page, or the token header type is not accepted. Try the exact GitLab API URL ending in /api/v4 and switch Auth to Bearer or Both."
            if url:
                hints += f" Response URL: {url}"
            return hints[:900]
        compact = re.sub(r"\s+", " ", text).strip()
        return compact[:900] or "GitLab API returned an empty non-JSON response."

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
        iteration_id: str | int | None = None,
        milestone: str | None = None,
        assignee_username: str | None = None,
        page: int = 1,
        per_page: int = 50,
    ) -> dict:
        res = self._request("GET", f"/projects/{self._project(project)}/issues", params={
            "state": state,
            "labels": labels,
            "search": search,
            "iteration_id": iteration_id,
            "milestone": milestone,
            "assignee_username": assignee_username,
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
        iteration_id: str | int | None = None,
        milestone: str | None = None,
        assignee_username: str | None = None,
        page: int = 1,
        per_page: int = 50,
    ) -> dict:
        res = self._request("GET", f"/groups/{self._group(group)}/issues", params={
            "state": state,
            "labels": labels,
            "search": search,
            "iteration_id": iteration_id,
            "milestone": milestone,
            "assignee_username": assignee_username,
            "scope": "all",
            "order_by": "updated_at",
            "sort": "desc",
            "page": page,
            "per_page": min(max(int(per_page or 50), 1), 100),
        })
        if res.get("ok"):
            res["issues"] = [self._issue(i) for i in (res.get("data") or [])]
        return res

    def list_group_iterations(self, group: str | int, state: str = "current") -> dict:
        res = self._request("GET", f"/groups/{self._group(group)}/iterations", params={
            "state": state,
            "include_ancestors": "true",
            "per_page": 20,
        })
        if res.get("ok"):
            res["iterations"] = [self._iteration(i) for i in (res.get("data") or [])]
        return res

    def get_project_issue(self, project: str | int, issue_iid: int) -> dict:
        res = self._request("GET", f"/projects/{self._project(project)}/issues/{int(issue_iid)}")
        if res.get("ok"):
            res["issue"] = self._issue(res.get("data") or {})
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
            "iteration": issue.get("iteration") or {},
            "milestone": issue.get("milestone") or {},
        }

    @staticmethod
    def _iteration(iteration: dict) -> dict:
        return {
            "id": iteration.get("id"),
            "iid": iteration.get("iid"),
            "title": iteration.get("title"),
            "state": iteration.get("state"),
            "start_date": iteration.get("start_date"),
            "due_date": iteration.get("due_date"),
            "web_url": iteration.get("web_url"),
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
