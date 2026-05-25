"""
copilot_backend.py
==================
Thin async wrapper around the official GitHub Copilot SDK (`github-copilot-sdk`,
imported as `copilot`). It starts the Copilot agent subprocess, opens a streaming
session, and forwards incremental response chunks to callbacks supplied by the UI.

This uses GitHub's *supported* SDK path -- it authenticates with your own Copilot
login (or a token you provide), so it works with a Copilot Business seat without
impersonating an editor or hitting internal endpoints.

API shapes here were verified against github-copilot-sdk 0.3.0.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import uuid

from copilot import CopilotClient, SubprocessConfig


def _dbg(*parts):
    if os.environ.get("COPILOT_DEBUG"):
        print(f"[dbg {time.strftime('%H:%M:%S')}]", *parts, file=sys.stderr, flush=True)
from copilot.generated.session_events import SessionEventType
from copilot.generated.rpc import (ModeSetRequest, SessionMode, SessionsForkRequest,
                                   MCPDiscoverRequest)
from copilot.session import PermissionRequestResult


class CopilotBackend:
    def __init__(self, github_token: str | None = None, model: str | None = None,
                 working_dir: str | None = None):
        # If github_token is None we fall back to the user's logged-in Copilot
        # session (the same auth `gh`/the CLI uses).
        self.github_token = github_token
        self.model = model
        self.working_dir = working_dir   # folder Copilot may read/run commands in
        self.instructions = ""           # custom instructions (system message, append mode)
        self.mcp_servers = None          # dict[str, MCPServerConfig] for MCP tools
        self.mcp_status = {}             # name -> {status, error} from session events
        self.mcp_disabled = set()        # names the user has toggled off
        self.mode = "interactive"        # interactive | plan | autopilot
        self.authenticated = False       # whether the Copilot session is signed in
        self.login = None                # GitHub login when authenticated
        # Per-kind permission policy (allow | ask | deny). read is always allowed.
        self.perm_rules = {"write": "ask", "shell": "ask", "url": "ask",
                           "mcp": "ask", "memory": "allow", "hook": "ask"}
        self._user_event_ids = []        # ids of user.message events, for undo
        # Discover instructions + MCP servers from .github / ~/.copilot. Discovered
        # MCP tools count toward the Copilot API's 128-tool-per-request cap, so this
        # can be toggled off (or via COPILOT_NO_DISCOVERY=1) to stay under the limit.
        self.config_discovery = (os.environ.get("COPILOT_NO_DISCOVERY", "").lower()
                                 not in ("1", "true", "yes"))
        self.client: CopilotClient | None = None
        self.session = None
        self._on_delta = None
        self._on_done = None
        self._on_error = None
        self._on_activity = None  # reasoning / tool / command activity
        self.last_quota = None  # latest quota seen via assistant.usage events
        self.commands = []      # Copilot slash commands from commands.changed events
        self.auto_approve = False        # when True, permission requests are allowed silently
        self._pending = {}               # request_id -> asyncio.Future awaiting a user decision
        self._on_permission = None       # UI callback for a permission prompt

    def set_handlers(self, on_delta, on_done, on_error, on_activity=None, on_permission=None):
        """Register UI callbacks. on_delta(text), on_done(), on_error(msg), on_activity(dict), on_permission(dict)."""
        self._on_delta = on_delta
        self._on_done = on_done
        self._on_error = on_error
        self._on_activity = on_activity
        self._on_permission = on_permission

    def set_auto_approve(self, value: bool):
        self.auto_approve = bool(value)

    @staticmethod
    def _perm_field(req, name, default=None):
        obj = getattr(req, "permission_request", None) or getattr(req, "request", None) or req
        return getattr(obj, name, default)

    def _describe_permission(self, req):
        k = self._perm_field(req, "kind", "")
        kind = getattr(k, "value", str(k))
        title = {
            "shell": "Run a shell command", "write": "Edit a file", "read": "Read a file",
            "url": "Access a URL", "mcp": "Use an MCP tool", "custom-tool": "Use a tool",
            "memory": "Update memory", "hook": "Run a hook",
        }.get(kind, "Permission request")
        detail = (self._perm_field(req, "full_command_text")
                  or self._perm_field(req, "file_name")
                  or self._perm_field(req, "path") or "")
        if not detail:
            cmds = self._perm_field(req, "commands")
            if cmds:
                detail = ", ".join(getattr(c, "identifier", str(c)) for c in cmds)
        if not detail:
            urls = self._perm_field(req, "possible_urls")
            if urls:
                detail = ", ".join(getattr(u, "url", str(u)) for u in urls)
        reason = self._perm_field(req, "intention") or self._perm_field(req, "reason") or ""
        can_session = self._perm_field(req, "can_offer_session_approval", True)
        diff = self._perm_field(req, "diff", "") or ""
        file_name = self._perm_field(req, "file_name", "") or ""
        return {"kind": kind, "title": title, "detail": str(detail),
                "reason": str(reason), "canSession": bool(can_session),
                "diff": str(diff), "file": str(file_name)}

    async def _handle_permission(self, req, invocation=None):
        kind = getattr(self._perm_field(req, "kind", ""), "value", "") or ""
        read_only = bool(self._perm_field(req, "read_only", False))
        is_read = (kind == "read") or read_only
        # Plan mode is read-only: deny anything that changes state.
        if self.mode == "plan" and not is_read:
            return PermissionRequestResult(kind="reject")
        if self.auto_approve or is_read:
            return PermissionRequestResult(kind="approve-once")
        policy = self.perm_rules.get(kind, "ask")
        if policy == "allow":
            return PermissionRequestResult(kind="approve-once")
        if policy == "deny":
            return PermissionRequestResult(kind="reject")
        if not self._on_permission:
            return PermissionRequestResult(kind="approve-once")  # no UI present
        loop = asyncio.get_event_loop()
        fut = loop.create_future()
        rid = uuid.uuid4().hex
        self._pending[rid] = fut
        payload = self._describe_permission(req)
        payload["id"] = rid
        try:
            self._on_permission(payload)
            decision = await asyncio.wait_for(fut, timeout=180)
        except Exception:
            decision = "user-not-available"
        finally:
            self._pending.pop(rid, None)
        if decision == "approve-all":
            self.auto_approve = True
            decision = "approve-once"
        if decision not in ("approve-once", "reject"):
            decision = "user-not-available"
        return PermissionRequestResult(kind=decision)

    def resolve_permission(self, rid, decision):
        fut = self._pending.get(rid)
        if fut and not fut.done():
            fut.get_loop().call_soon_threadsafe(fut.set_result, decision)

    async def start(self):
        # Each step is bounded so a proxy/network stall surfaces a specific error
        # in the UI instead of hanging forever. The labels match the diagnose.py steps.
        self.client = CopilotClient(self._subprocess_cfg(), auto_start=False)
        _dbg("backend.start(): client.start() ...")
        try:
            await asyncio.wait_for(self.client.start(), timeout=45)
        except asyncio.TimeoutError:
            raise RuntimeError("Timed out launching the bundled Copilot CLI (check proxy settings).")
        _dbg("backend.start(): get_auth_status() ...")
        try:
            status = await asyncio.wait_for(self.client.get_auth_status(), timeout=45)
        except asyncio.TimeoutError:
            raise RuntimeError("Timed out checking sign-in status (proxy or auth handshake stalled).")
        self.authenticated = bool(getattr(status, "isAuthenticated", False))
        self.login = getattr(status, "login", None)
        _dbg("backend.start(): authenticated =", self.authenticated, "login =", self.login)
        # Only create a session once authenticated -- an unauthenticated session is
        # created but fails on send ("Session was not created with auth info").
        if self.authenticated:
            _dbg("backend.start(): create_session() ...")
            try:
                self.session = await asyncio.wait_for(self._make_session(), timeout=60)
            except asyncio.TimeoutError:
                raise RuntimeError("Signed in, but timed out creating a session (proxy reach to GitHub).")
            _dbg("backend.start(): session created")
        else:
            self.session = None
        return status

    def _subprocess_cfg(self):
        kwargs = dict(
            github_token=self.github_token,
            use_logged_in_user=(self.github_token is None),
            cwd=self.working_dir,
        )
        # Use the SDK's *bundled* copilot binary by default — its protocol version
        # matches this SDK. We deliberately do NOT use the ambient COPILOT_EXE (e.g.
        # a newer system-installed copilot.exe), because a newer CLI's handshake can
        # break the installed SDK's parser ("invalid literal for int()" on ping).
        # To force a specific, protocol-matched binary, set COPILOT_DESKTOP_CLI.
        cli_path = os.environ.get("COPILOT_DESKTOP_CLI")
        if cli_path and os.path.isfile(cli_path):
            kwargs["cli_path"] = cli_path
        return SubprocessConfig(**kwargs)

    async def set_mode(self, mode):
        """Switch agent mode live (no restart) via the per-session mode RPC."""
        self.mode = mode
        if self.session:
            try:
                await self.session.rpc.mode.set(ModeSetRequest(mode=SessionMode(mode)))
            except Exception:
                pass

    def set_perm_rules(self, rules):
        if isinstance(rules, dict):
            self.perm_rules.update({k: v for k, v in rules.items() if v in ("allow", "ask", "deny")})

    async def undo(self):
        """Rewind the last turn by forking the session to just before the last
        user message and resuming the fork as the active session."""
        if not (self.client and self.session and self._user_event_ids):
            return {"ok": False, "error": "Nothing to undo"}
        target = self._user_event_ids[-1]
        sid = getattr(self.session, "session_id", None)
        if not sid or not target:
            return {"ok": False, "error": "No undo point"}
        res = await self.client.rpc.sessions.fork(
            SessionsForkRequest(session_id=sid, to_event_id=target))
        new_id = getattr(res, "session_id", None)
        if not new_id:
            return {"ok": False, "error": "Fork failed"}
        self.session = await self.client.resume_session(
            new_id, on_permission_request=self._handle_permission,
            on_event=self._handle_event, streaming=True,
            working_directory=self.working_dir)
        self._user_event_ids.pop()
        if self.mode and self.mode != "interactive":
            try:
                await self.session.rpc.mode.set(ModeSetRequest(mode=SessionMode(self.mode)))
            except Exception:
                pass
        return {"ok": True}

    async def _make_session(self):
        # Config discovery loads instructions + MCP servers from .github and the
        # user's ~/.copilot. Discovered MCP tools count toward the API's 128-tool
        # cap and an unreachable server can stall session creation, so it can be
        # turned off at runtime (Settings) or via COPILOT_NO_DISCOVERY=1.
        discover = self.config_discovery
        kwargs = dict(
            on_permission_request=self._handle_permission,
            model=self.model,
            streaming=True,
            on_event=self._handle_event,
            working_directory=self.working_dir,
            enable_config_discovery=discover,   # honor repo .github + ~/.copilot config
        )
        if self.instructions:
            kwargs["system_message"] = {"mode": "append", "content": self.instructions}
        if self.mcp_servers:
            active = {n: c for n, c in self.mcp_servers.items() if n not in self.mcp_disabled}
            if active:
                kwargs["mcp_servers"] = active
        sess = await self.client.create_session(**kwargs)
        if self.mode and self.mode != "interactive":
            try:
                await sess.rpc.mode.set(ModeSetRequest(mode=SessionMode(self.mode)))
            except Exception:
                pass
        return sess

    async def _recreate(self):
        self.commands = []
        if self.client:
            self.session = await self._make_session()

    async def set_working_dir(self, path):
        self.working_dir = path
        await self._recreate()

    async def set_config_discovery(self, on):
        self.config_discovery = bool(on)
        await self._recreate()

    async def set_instructions(self, text):
        self.instructions = text or ""
        await self._recreate()

    async def discover_mcp(self):
        """List MCP servers configured across all sources (.github, ~/.copilot,
        plugins, builtin) — independent of runtime load, so the UI can show them
        even before any session activity. Returns JSON-safe dicts."""
        if not self.client:
            return []
        try:
            res = await self.client.rpc.mcp.discover(
                MCPDiscoverRequest(working_directory=self.working_dir))
        except Exception:
            return []
        out = []
        for s in (getattr(res, "servers", None) or []):
            out.append({
                "name": getattr(s, "name", ""),
                "enabled": bool(getattr(s, "enabled", True)),
                "source": getattr(getattr(s, "source", None), "value", None) or "",
                "type": getattr(getattr(s, "type", None), "value", None) or "",
            })
        return out

    async def set_mcp_servers(self, servers):
        self.mcp_servers = servers or None
        # drop disabled/status entries for servers no longer present
        names = set((servers or {}).keys())
        self.mcp_disabled &= names
        self.mcp_status = {k: v for k, v in self.mcp_status.items() if k in names}
        await self._recreate()

    async def set_mcp_enabled(self, name, enabled):
        if enabled:
            self.mcp_disabled.discard(name)
        else:
            self.mcp_disabled.add(name)
            self.mcp_status[name] = {"status": "disabled", "error": None}
        await self._recreate()

    def _handle_event(self, event):
        """Called by the SDK for every session event. Sync callback."""
        try:
            t = event.type
            _dbg("event:", getattr(t, "value", t))
            if t == SessionEventType.USER_MESSAGE:
                eid = getattr(event, "id", None)
                if eid is not None:
                    self._user_event_ids.append(str(eid))
            if t == SessionEventType.ASSISTANT_MESSAGE_DELTA:
                if self._on_delta:
                    self._on_delta(event.data.delta_content)
            elif t == SessionEventType.SESSION_IDLE:
                # Fires when the whole response is complete. A single user request
                # can span multiple turns (think -> tool -> new turn -> answer), so
                # finalize on idle, not on each turn_end.
                if self._on_done:
                    self._on_done()
            elif t == SessionEventType.ASSISTANT_USAGE:
                snaps = getattr(event.data, "quota_snapshots", None)
                if snaps:
                    self.last_quota = self._snaps_to_dict(snaps)
            elif t == SessionEventType.COMMANDS_CHANGED:
                cmds = getattr(event.data, "commands", None) or []
                self.commands = [
                    {"name": c.name, "description": getattr(c, "description", None)}
                    for c in cmds
                ]
            elif t == SessionEventType.ASSISTANT_REASONING_DELTA:
                if self._on_activity:
                    self._on_activity({"kind": "reasoning_delta", "text": getattr(event.data, "delta_content", "")})
            elif t == SessionEventType.ASSISTANT_REASONING:
                if self._on_activity:
                    self._on_activity({"kind": "reasoning_done"})
            elif t == SessionEventType.TOOL_EXECUTION_START:
                d = event.data
                args = getattr(d, "arguments", None)
                detail = ""
                if isinstance(args, dict):
                    detail = (args.get("command") or args.get("cmd") or args.get("path")
                              or args.get("filePath") or args.get("file_path") or "")
                    if not detail:
                        import json as _j
                        try: detail = _j.dumps(args)
                        except Exception: detail = str(args)
                if self._on_activity:
                    self._on_activity({"kind": "tool", "name": getattr(d, "tool_name", "tool"),
                                       "id": getattr(d, "tool_call_id", ""), "detail": str(detail)[:400],
                                       "mcp": getattr(d, "mcp_server_name", None)})
            elif t == SessionEventType.TOOL_EXECUTION_COMPLETE:
                d = event.data
                if self._on_activity:
                    self._on_activity({"kind": "tool_done", "id": getattr(d, "tool_call_id", ""),
                                       "success": bool(getattr(d, "success", True))})
            elif t == SessionEventType.EXTERNAL_TOOL_REQUESTED:
                if self._on_activity:
                    self._on_activity({"kind": "tool", "name": getattr(event.data, "tool_name", ""), "id": getattr(event.data, "request_id", "")})
            elif t == SessionEventType.EXTERNAL_TOOL_COMPLETED:
                if self._on_activity:
                    self._on_activity({"kind": "tool_done", "id": getattr(event.data, "request_id", "")})
            elif t == SessionEventType.COMMAND_EXECUTE:
                if self._on_activity:
                    self._on_activity({"kind": "command", "cmd": getattr(event.data, "command", ""), "name": getattr(event.data, "command_name", "")})
            elif t == SessionEventType.SESSION_MCP_SERVERS_LOADED:
                for srv in (getattr(event.data, "servers", None) or []):
                    self.mcp_status[srv.name] = {
                        "status": getattr(getattr(srv, "status", None), "value", None),
                        "error": getattr(srv, "error", None)}
                if self._on_activity:
                    self._on_activity({"kind": "mcp_status", "servers": self.mcp_status})
            elif t == SessionEventType.SESSION_MCP_SERVER_STATUS_CHANGED:
                nm = getattr(event.data, "server_name", None)
                st = getattr(getattr(event.data, "status", None), "value", None)
                if nm:
                    self.mcp_status.setdefault(nm, {})["status"] = st
                if self._on_activity:
                    self._on_activity({"kind": "mcp_status", "servers": self.mcp_status})
            elif t == SessionEventType.SESSION_USAGE_INFO:
                d = event.data
                if self._on_activity:
                    self._on_activity({"kind": "context",
                                       "current": getattr(d, "current_tokens", None),
                                       "limit": getattr(d, "token_limit", None)})
            elif t == SessionEventType.SESSION_ERROR:
                msg = getattr(event.data, "message", None) or str(event.data)
                if self._on_error:
                    self._on_error(str(msg))
        except Exception as e:  # never let a handler crash the SDK loop
            if self._on_error:
                self._on_error(f"event handler error: {e}")

    @staticmethod
    def _snaps_to_dict(snaps):
        """Normalize a quota-snapshot dict (from either account.getQuota or an
        assistant.usage event -- both share these field names)."""
        out = {}
        for key, q in (snaps or {}).items():
            rd = getattr(q, "reset_date", None)
            out[key] = {
                "entitlement": getattr(q, "entitlement_requests", None),
                "used": getattr(q, "used_requests", None),
                "remaining_percentage": getattr(q, "remaining_percentage", None),
                "unlimited": getattr(q, "is_unlimited_entitlement", None),
                "overage": getattr(q, "overage", None),
                "reset_date": str(rd) if rd else None,
            }
        return out

    async def abort(self):
        """Cancel the in-flight assistant turn (Stop button)."""
        if self.session:
            await self.session.abort()

    async def compact(self):
        """Summarize history to reduce context-window usage (CLI /compact)."""
        if self.session:
            await self.session.rpc.history.compact()

    async def send(self, prompt: str, attachments=None):
        if not self.session:
            raise RuntimeError("Session not started -- call start() first.")
        # attachments: list of {"type":"file","path":...,"displayName":...} dicts,
        # which match the SDK's FileAttachment TypedDict at runtime.
        await self.session.send(prompt, attachments=attachments or None)

    async def list_models(self):
        if not self.client:
            return []
        return await self.client.list_models()

    async def get_quota(self):
        """Per-category Copilot usage/quota (chat, completions, premium requests),
        same data VSCode shows. Returns a JSON-safe dict keyed by category.

        Tries the experimental account.getQuota endpoint first; if it returns
        nothing (common with global/logged-in auth), falls back to the latest
        snapshot captured from assistant.usage events after a request."""
        data = {}
        if self.client:
            try:
                res = await self.client.rpc.account.get_quota()
                data = self._snaps_to_dict(getattr(res, "quota_snapshots", None))
            except Exception:
                data = {}
        if not data and self.last_quota:
            data = self.last_quota
        return data

    async def set_model(self, model: str, reasoning: str | None = None):
        if self.session:
            if reasoning:
                await self.session.set_model(model, reasoning_effort=reasoning)
            else:
                await self.session.set_model(model)
            self.model = model

    async def stop(self):
        if self.client:
            await self.client.stop()
