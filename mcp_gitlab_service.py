"""Minimal GitLab MCP adapter.

This lets the app use an already-configured stdio GitLab MCP server directly for
planning panels, without requiring the GitHub Copilot session to be connected.
"""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import threading
from urllib.parse import urlparse


class MCPError(RuntimeError):
    pass


class StdioMCPClient:
    def __init__(self, command: str, args: list[str] | None = None, env: dict | None = None, timeout: int = 45):
        self.command = command
        self.args = args or []
        self.env = env or os.environ.copy()
        self.timeout = timeout
        self.proc = None
        self._id = 0
        self._responses: queue.Queue = queue.Queue()
        self._reader = None
        self._stderr = None

    def __enter__(self):
        self.start()
        self.initialize()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def start(self):
        self.proc = subprocess.Popen(
            [self.command, *self.args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self.env,
            text=False,
        )
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._stderr = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr.start()

    def close(self):
        if not self.proc:
            return
        try:
            self.proc.terminate()
            self.proc.wait(timeout=3)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass

    def initialize(self):
        self.request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "copilot-desktop", "version": "0.1"},
        })
        self.notify("notifications/initialized", {})

    def list_tools(self) -> list[dict]:
        res = self.request("tools/list", {})
        return (res or {}).get("tools") or []

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        return self.request("tools/call", {"name": name, "arguments": arguments or {}})

    def request(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        req_id = self._id
        self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}})
        while True:
            try:
                msg = self._responses.get(timeout=self.timeout)
            except queue.Empty:
                raise MCPError(f"MCP request timed out: {method}")
            if msg.get("id") != req_id:
                continue
            if msg.get("error"):
                err = msg["error"]
                raise MCPError(err.get("message") if isinstance(err, dict) else str(err))
            return msg.get("result") or {}

    def notify(self, method: str, params: dict | None = None):
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def _send(self, payload: dict):
        if not (self.proc and self.proc.stdin):
            raise MCPError("MCP process is not running")
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.proc.stdin.write(f"Content-Length: {len(raw)}\r\n\r\n".encode("ascii") + raw)
        self.proc.stdin.flush()

    def _read_loop(self):
        while self.proc and self.proc.stdout:
            try:
                msg = self._read_message()
                if msg:
                    self._responses.put(msg)
            except Exception:
                break

    def _drain_stderr(self):
        while self.proc and self.proc.stderr:
            try:
                if not self.proc.stderr.readline():
                    break
            except Exception:
                break

    def _read_message(self) -> dict | None:
        length = None
        while True:
            line = self.proc.stdout.readline()
            if not line:
                return None
            if line in (b"\r\n", b"\n"):
                if length is not None:
                    break
                continue
            if line.lower().startswith(b"content-length:"):
                length = int(line.split(b":", 1)[1].strip())
        raw = self.proc.stdout.read(length)
        if not raw:
            return None
        return json.loads(raw.decode("utf-8"))


class GitLabMCPService:
    def __init__(self, settings_getter=None):
        self._settings_getter = settings_getter

    def _settings(self) -> dict:
        if not self._settings_getter:
            return {}
        data = self._settings_getter() or {}
        return data if isinstance(data, dict) else {}

    def status(self) -> dict:
        try:
            config_path, server_name, server = self._server_config()
            with self._client(server) as client:
                tools = client.list_tools()
            return {"ok": True, "config_path": config_path, "server": server_name, "tools": [t.get("name") for t in tools]}
        except Exception as e:
            return {"ok": False, "error": str(e), "tools": []}

    def copilot_server_config(self) -> dict:
        """Return the selected GitLab MCP server in the shape expected by the
        Copilot SDK. Unlike the direct status/search path, this config is handed
        to the Copilot CLI so chat can call the actual MCP tools."""
        config_path, server_name, server = self._server_config()
        cfg = self._normalized_server_config(server, include_settings_env=True)
        return {"ok": True, "config_path": config_path, "server": server_name, "mcp_servers": {server_name: cfg}}

    def current_sprint(self, group=None, assignee=None, labels=None) -> dict:
        group = group or self._settings().get("gitlab_group")
        if not group:
            return {"ok": False, "error": "Set a GitLab group id/path for sprint MCP calls.", "issues": []}
        try:
            with self._open() as (client, tools):
                iteration_tool = self._find_tool(tools, ["list_group_iterations", "list_iterations", "get_current_iteration", "current_iteration"])
                if iteration_tool:
                    iteration_res = self._call_with_variants(client, iteration_tool, [
                        {"group_id": group, "state": "current"},
                        {"groupId": group, "state": "current"},
                        {"group": group, "state": "current"},
                    ])
                    iterations = self._as_list(iteration_res, ("iterations", "data", "items"))
                    current = self._normalize_iteration(iterations[0]) if iterations else None
                    if current:
                        issues = self._issues_for_group(client, tools, group, {
                            "iteration_id": current.get("id"),
                            "assignee_username": assignee,
                            "labels": labels,
                            "state": "opened",
                        })
                        return {"ok": True, "source": "mcp", "group": group, "iteration": current, "issues": issues}
                sprint_tool = self._find_tool(tools, ["list_current_sprint_issues", "get_current_sprint_issues", "current_sprint_issues", "list_sprint_issues"])
                if sprint_tool:
                    res = self._call_with_variants(client, sprint_tool, [
                        {"group_id": group, "assignee_username": assignee, "labels": labels},
                        {"groupId": group, "assigneeUsername": assignee, "labels": labels},
                        {"group": group, "assignee": assignee, "labels": labels},
                    ])
                    return {"ok": True, "source": "mcp", "group": group, "iteration": None, "issues": self._normalize_issues(res)}
            return {"ok": False, "error": "The configured GitLab MCP server does not expose iteration/current sprint tools.", "issues": []}
        except Exception as e:
            return {"ok": False, "error": str(e), "issues": []}

    def search_stories(self, query="", target=None, scope="project", state="opened", labels=None, assignee=None) -> dict:
        target = target or (self._settings().get("gitlab_group") if scope == "group" else self._settings().get("gitlab_project"))
        if not target:
            return {"ok": False, "error": f"Set a GitLab {scope} id/path for MCP search.", "issues": []}
        try:
            with self._open() as (client, tools):
                if scope == "group":
                    raw = self._issues_for_group(client, tools, target, {
                        "search": query,
                        "state": state,
                        "labels": labels,
                        "assignee_username": assignee,
                    }, raw=True)
                else:
                    raw = self._issues_for_project(client, tools, target, {
                        "search": query,
                        "state": state,
                        "labels": labels,
                        "assignee_username": assignee,
                    }, raw=True)
            return {"ok": True, "source": "mcp", "scope": scope, "target": target, "issues": self._normalize_issues(raw)}
        except Exception as e:
            return {"ok": False, "error": str(e), "issues": []}

    def get_issue(self, target, issue_iid) -> dict:
        target = target or self._settings().get("gitlab_project")
        if not target:
            return {"ok": False, "error": "Set a GitLab project id/path to load a story."}
        try:
            with self._open() as (client, tools):
                tool = self._find_tool(tools, ["get_project_issue", "get_issue", "read_issue", "get_gitlab_issue"])
                if not tool:
                    raise MCPError("The configured GitLab MCP server does not expose a get_issue tool.")
                raw = self._call_with_variants(client, tool, [
                    {"project_id": target, "issue_iid": int(issue_iid)},
                    {"projectId": target, "issueIid": int(issue_iid)},
                    {"project": target, "iid": int(issue_iid)},
                    {"id": target, "issue_iid": int(issue_iid)},
                ])
            issue = self._normalize_issue(raw.get("issue") if isinstance(raw, dict) and raw.get("issue") else raw)
            return {"ok": True, "source": "mcp", "target": target, "issue": issue}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _open(self):
        service = self

        class _Ctx:
            def __enter__(self):
                _, _, server = service._server_config()
                self.client = StdioMCPClient(*service._command(server), env=service._env(server))
                self.client.__enter__()
                self.tools = self.client.list_tools()
                return self.client, self.tools

            def __exit__(self, exc_type, exc, tb):
                self.client.__exit__(exc_type, exc, tb)

        return _Ctx()

    def _server_config(self) -> tuple[str, str, dict]:
        path = self._config_path()
        if not path:
            raise MCPError("Set GitLab MCP config path in the GitLab connection panel.")
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
        servers = data.get("mcpServers") or data.get("servers") or {}
        if not isinstance(servers, dict) or not servers:
            raise MCPError("No MCP servers found in config.")
        desired = str(self._settings().get("gitlab_mcp_server") or "").strip()
        if desired and desired in servers:
            return path, desired, servers[desired]
        for preferred in ("GitLab-MCP", "gitlab", "GitLab"):
            for name, cfg in servers.items():
                if name.lower() == preferred.lower():
                    return path, name, cfg
        for name, cfg in servers.items():
            if "gitlab" in name.lower():
                return path, name, cfg
        name = next(iter(servers))
        return path, name, servers[name]

    def _config_path(self) -> str | None:
        settings = self._settings()
        candidates = [
            settings.get("gitlab_mcp_config"),
            os.environ.get("GITLAB_MCP_CONFIG"),
            os.environ.get("COPILOT_MCP_CONFIG"),
            os.environ.get("MCP_CONFIG_FILE"),
            os.path.join(os.getcwd(), "mcp-config.json"),
            os.path.join(os.path.expanduser("~"), ".copilot", "mcp-config.json"),
            os.path.join(os.path.expanduser("~"), "OneDrive - UBS", "projects", "devpod", "mcp-config.json"),
        ]
        for path in candidates:
            if not path:
                continue
            expanded = os.path.abspath(os.path.expandvars(os.path.expanduser(str(path))))
            if os.path.isfile(expanded):
                return expanded
        return None

    def _command(self, server: dict) -> tuple[str, list[str]]:
        args = [str(a) for a in (server.get("args") or [])]
        command = server.get("command")
        if not command and args:
            command, args = args[0], args[1:]
        if not command:
            raise MCPError("MCP server config is missing command.")
        command = str(command)
        if os.name == "nt" and command.lower().endswith((".cmd", ".bat")):
            return os.environ.get("ComSpec") or "cmd.exe", ["/c", command, *args]
        return command, args

    def _normalized_server_config(self, server: dict, include_settings_env: bool = True) -> dict:
        command, args = self._command(server)
        cfg: dict = {"command": command, "args": args}
        if server.get("cwd"):
            cfg["cwd"] = str(server.get("cwd"))
        env = dict(server.get("env") or {})
        if include_settings_env:
            env.update(self._settings_env_overrides())
        env = {str(k): str(v) for k, v in env.items() if v is not None}
        if env:
            cfg["env"] = env
        tools = server.get("tools")
        if isinstance(tools, list):
            cfg["tools"] = [str(t) for t in tools]
        if server.get("timeout"):
            try:
                cfg["timeout"] = int(server.get("timeout"))
            except Exception:
                pass
        typ = str(server.get("type") or "").strip().lower()
        if typ in ("local", "stdio", "http", "sse"):
            cfg["type"] = typ
        return cfg

    def _env(self, server: dict) -> dict:
        env = os.environ.copy()
        env.update({str(k): str(v) for k, v in (server.get("env") or {}).items()})
        env.update(self._settings_env_overrides())
        return env

    def _settings_env_overrides(self) -> dict:
        env: dict[str, str] = {}
        settings = self._settings()
        token = str(settings.get("gitlab_token") or "").strip()
        if token:
            env.setdefault("GITLAB_TOKEN", token)
            env.setdefault("GITLAB_PERSONAL_ACCESS_TOKEN", token)
            env.setdefault("GL_TOKEN", token)
        url = str(settings.get("gitlab_url") or "").strip()
        if url:
            api = url.rstrip("/")
            if not api.endswith("/api/v4"):
                api += "/api/v4"
            env.setdefault("GITLAB_API_URL", api)
        if settings.get("gitlab_project"):
            env.setdefault("GITLAB_PROJECT_ID", str(settings.get("gitlab_project")))
        if settings.get("gitlab_group"):
            env.setdefault("GITLAB_GROUP_ID", str(settings.get("gitlab_group")))
        return env

    def _client(self, server: dict) -> StdioMCPClient:
        command, args = self._command(server)
        return StdioMCPClient(command, args, self._env(server))

    def _issues_for_group(self, client, tools, group, extra, raw=False):
        tool = self._find_tool(tools, ["list_group_issues", "search_group_issues", "list_issues", "search_issues", "get_issues"])
        if not tool:
            raise MCPError("The configured GitLab MCP server does not expose an issue search/list tool.")
        res = self._call_with_variants(client, tool, [
            {"group_id": group, **extra},
            {"groupId": group, **self._camel_extra(extra)},
            {"group": group, **extra},
        ])
        return res if raw else self._normalize_issues(res)

    def _issues_for_project(self, client, tools, project, extra, raw=False):
        tool = self._find_tool(tools, ["list_project_issues", "search_project_issues", "list_issues", "search_issues", "get_issues"])
        if not tool:
            raise MCPError("The configured GitLab MCP server does not expose an issue search/list tool.")
        res = self._call_with_variants(client, tool, [
            {"project_id": project, **extra},
            {"projectId": project, **self._camel_extra(extra)},
            {"project": project, **extra},
            {"id": project, **extra},
        ])
        return res if raw else self._normalize_issues(res)

    @staticmethod
    def _camel_extra(extra: dict) -> dict:
        mapping = {"iteration_id": "iterationId", "assignee_username": "assigneeUsername"}
        return {mapping.get(k, k): v for k, v in (extra or {}).items()}

    def _call_with_variants(self, client, tool_name, variants):
        last_error = None
        for args in variants:
            try:
                return self._tool_result(client.call_tool(tool_name, self._clean_args(args)))
            except Exception as e:
                last_error = e
        raise MCPError(str(last_error) if last_error else f"Could not call MCP tool {tool_name}")

    @staticmethod
    def _find_tool(tools: list[dict], candidates: list[str]) -> str | None:
        names = [t.get("name") for t in tools if t.get("name")]
        norm = {re.sub(r"[^a-z0-9]", "", n.lower()): n for n in names}
        for candidate in candidates:
            c = re.sub(r"[^a-z0-9]", "", candidate.lower())
            if c in norm:
                return norm[c]
        for candidate in candidates:
            c = re.sub(r"[^a-z0-9]", "", candidate.lower())
            for n in names:
                nn = re.sub(r"[^a-z0-9]", "", n.lower())
                if nn.endswith(c) or c in nn:
                    return n
        return None

    @staticmethod
    def _clean_args(args: dict) -> dict:
        return {k: v for k, v in args.items() if v not in (None, "")}

    def _tool_result(self, result: dict):
        if result.get("isError"):
            raise MCPError(self._content_text(result) or "MCP tool returned an error.")
        if result.get("structuredContent") is not None:
            return result["structuredContent"]
        text = self._content_text(result)
        parsed = self._parse_json_text(text)
        return parsed if parsed is not None else {"text": text}

    @staticmethod
    def _content_text(result: dict) -> str:
        content = result.get("content") or []
        parts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
        return "\n".join(parts).strip()

    @staticmethod
    def _parse_json_text(text: str):
        if not text:
            return None
        try:
            return json.loads(text)
        except Exception:
            pass
        match = re.search(r"(\{.*\}|\[.*\])", text, flags=re.S)
        if match:
            try:
                return json.loads(match.group(1))
            except Exception:
                return None
        return None

    @staticmethod
    def _as_list(value, keys=()):
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            for key in keys:
                if isinstance(value.get(key), list):
                    return value[key]
        return []

    def _normalize_issues(self, raw) -> list[dict]:
        return [self._normalize_issue(i) for i in self._as_list(raw, ("issues", "data", "items", "results"))]

    @staticmethod
    def _normalize_issue(issue) -> dict:
        if not isinstance(issue, dict):
            return {"title": str(issue)}
        return {
            "id": issue.get("id"),
            "iid": issue.get("iid") or issue.get("issue_iid") or issue.get("issueIid"),
            "project_id": issue.get("project_id") or issue.get("projectId"),
            "title": issue.get("title") or "",
            "description": issue.get("description") or issue.get("body") or "",
            "state": issue.get("state"),
            "labels": issue.get("labels") or [],
            "assignees": [a.get("username") if isinstance(a, dict) else str(a) for a in (issue.get("assignees") or [])],
            "author": (issue.get("author") or {}).get("username") if isinstance(issue.get("author"), dict) else issue.get("author"),
            "web_url": issue.get("web_url") or issue.get("webUrl") or issue.get("url"),
            "updated_at": issue.get("updated_at") or issue.get("updatedAt"),
            "created_at": issue.get("created_at") or issue.get("createdAt"),
            "weight": issue.get("weight"),
            "due_date": issue.get("due_date") or issue.get("dueDate"),
            "issue_type": issue.get("issue_type") or issue.get("issueType") or "issue",
            "iteration": issue.get("iteration") or {},
            "milestone": issue.get("milestone") or {},
        }

    @staticmethod
    def _normalize_iteration(iteration) -> dict:
        if not isinstance(iteration, dict):
            return {"title": str(iteration)}
        return {
            "id": iteration.get("id"),
            "iid": iteration.get("iid"),
            "title": iteration.get("title") or iteration.get("name") or "Current iteration",
            "state": iteration.get("state"),
            "start_date": iteration.get("start_date") or iteration.get("startDate"),
            "due_date": iteration.get("due_date") or iteration.get("dueDate"),
            "web_url": iteration.get("web_url") or iteration.get("webUrl") or iteration.get("url"),
        }
