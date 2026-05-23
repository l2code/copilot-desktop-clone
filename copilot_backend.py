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
import uuid

from copilot import CopilotClient, SubprocessConfig
from copilot.generated.session_events import SessionEventType
from copilot.session import PermissionRequestResult


async def _approve_all(_request) -> PermissionRequestResult:
    """Auto-approve agent permission requests (tool/file/shell access).

    For a simple chat clone this is fine. If you later let Copilot run shell
    commands or edit files, tighten this to inspect `_request.kind` and prompt
    the user before returning 'approve-once' vs 'reject'.
    """
    return PermissionRequestResult(kind="approve-once")


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
        return {"kind": kind, "title": title, "detail": str(detail),
                "reason": str(reason), "canSession": bool(can_session)}

    async def _handle_permission(self, req):
        kind = getattr(self._perm_field(req, "kind", ""), "value", "")
        # Auto-allow read-only requests and everything once the user trusts the session.
        if self.auto_approve or kind == "read" or self._perm_field(req, "read_only", False):
            return PermissionRequestResult(kind="approve-once")
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
        cfg = SubprocessConfig(
            github_token=self.github_token,
            use_logged_in_user=(self.github_token is None),
            cwd=self.working_dir,
        )
        self.client = CopilotClient(cfg, auto_start=False)
        await self.client.start()

        # Surfaces whether Copilot auth succeeded; raised to the UI if not.
        status = await self.client.get_auth_status()

        self.session = await self._make_session()
        return status

    async def _make_session(self):
        kwargs = dict(
            on_permission_request=self._handle_permission,
            model=self.model,
            streaming=True,
            on_event=self._handle_event,
            working_directory=self.working_dir,
            enable_config_discovery=True,   # honor repo AGENTS.md / .github instructions
        )
        if self.instructions:
            kwargs["system_message"] = {"mode": "append", "content": self.instructions}
        if self.mcp_servers:
            kwargs["mcp_servers"] = self.mcp_servers
        return await self.client.create_session(**kwargs)

    async def _recreate(self):
        self.commands = []
        if self.client:
            self.session = await self._make_session()

    async def set_working_dir(self, path):
        self.working_dir = path
        await self._recreate()

    async def set_instructions(self, text):
        self.instructions = text or ""
        await self._recreate()

    async def set_mcp_servers(self, servers):
        self.mcp_servers = servers or None
        await self._recreate()

    def _handle_event(self, event):
        """Called by the SDK for every session event. Sync callback."""
        try:
            t = event.type
            if t == SessionEventType.ASSISTANT_MESSAGE_DELTA:
                if self._on_delta:
                    self._on_delta(event.data.delta_content)
            elif t == SessionEventType.ASSISTANT_TURN_END:
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
            elif t == SessionEventType.EXTERNAL_TOOL_REQUESTED:
                if self._on_activity:
                    self._on_activity({"kind": "tool", "name": getattr(event.data, "tool_name", ""), "id": getattr(event.data, "request_id", "")})
            elif t == SessionEventType.EXTERNAL_TOOL_COMPLETED:
                if self._on_activity:
                    self._on_activity({"kind": "tool_done", "id": getattr(event.data, "request_id", "")})
            elif t == SessionEventType.COMMAND_EXECUTE:
                if self._on_activity:
                    self._on_activity({"kind": "command", "cmd": getattr(event.data, "command", ""), "name": getattr(event.data, "command_name", "")})
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
