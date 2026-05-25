"""GitHub REST API helpers using public endpoints and user-provided auth."""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request


API_ROOT = "https://api.github.com"


def _run_gh_token() -> str | None:
    try:
        cp = subprocess.run(
            ["gh", "auth", "token"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
        token = cp.stdout.strip()
        return token if cp.returncode == 0 and token else None
    except Exception:
        return None


class GitHubService:
    def discover_token(self) -> tuple[str | None, str | None]:
        if os.environ.get("GITHUB_TOKEN"):
            return os.environ["GITHUB_TOKEN"], "GITHUB_TOKEN"
        if os.environ.get("GH_TOKEN"):
            return os.environ["GH_TOKEN"], "GH_TOKEN"
        token = _run_gh_token()
        if token:
            return token, "gh auth token"
        return None, None

    def auth_status(self) -> dict:
        token, source = self.discover_token()
        if not token:
            return {"ok": True, "authenticated": False, "source": None}
        res = self._request("GET", "/user")
        if not res.get("ok"):
            return {"ok": True, "authenticated": False, "source": source, "error": res.get("error")}
        user = res.get("data") or {}
        return {
            "ok": True,
            "authenticated": True,
            "source": source,
            "login": user.get("login"),
            "avatar_url": user.get("avatar_url"),
        }

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        token, _ = self.discover_token()
        if not token:
            return {"ok": False, "error": "GitHub authentication is not configured."}
        data = None
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "copilot-desktop-clone",
            "Authorization": f"Bearer {token}",
        }
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(API_ROOT + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                return {"ok": True, "data": json.loads(raw) if raw else None}
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(msg)
                msg = parsed.get("message") or msg
            except Exception:
                pass
            return {"ok": False, "status": e.code, "error": msg}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_repo(self, owner: str, repo: str) -> dict:
        return self._request("GET", f"/repos/{owner}/{repo}")

    def list_issues(self, owner: str, repo: str, state: str = "open") -> dict:
        res = self._request("GET", f"/repos/{owner}/{repo}/issues?state={state}&per_page=50")
        if res.get("ok"):
            items = []
            for issue in res.get("data") or []:
                if "pull_request" in issue:
                    continue
                items.append({
                    "number": issue.get("number"),
                    "title": issue.get("title"),
                    "state": issue.get("state"),
                    "author": (issue.get("user") or {}).get("login"),
                    "url": issue.get("html_url"),
                })
            res["issues"] = items
        return res

    def list_pull_requests(self, owner: str, repo: str, state: str = "open") -> dict:
        res = self._request("GET", f"/repos/{owner}/{repo}/pulls?state={state}&per_page=50")
        if res.get("ok"):
            res["pull_requests"] = [{
                "number": pr.get("number"),
                "title": pr.get("title"),
                "state": pr.get("state"),
                "author": (pr.get("user") or {}).get("login"),
                "head_branch": (pr.get("head") or {}).get("ref"),
                "base_branch": (pr.get("base") or {}).get("ref"),
                "url": pr.get("html_url"),
            } for pr in (res.get("data") or [])]
        return res

    def get_pull_request(self, owner: str, repo: str, number: int) -> dict:
        return self._request("GET", f"/repos/{owner}/{repo}/pulls/{int(number)}")

    def create_pull_request(self, owner: str, repo: str, title: str, body: str, base: str, head: str) -> dict:
        return self._request("POST", f"/repos/{owner}/{repo}/pulls", {
            "title": title,
            "body": body or "",
            "base": base,
            "head": head,
        })
