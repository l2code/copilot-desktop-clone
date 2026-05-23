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
    def __init__(self, github_token: str | None = None, model: str | None = None):
        # If github_token is None we fall back to the user's logged-in Copilot
        # session (the same auth `gh`/the CLI uses).
        self.github_token = github_token
        self.model = model
        self.client: CopilotClient | None = None
        self.session = None
        self._on_delta = None
        self._on_done = None
        self._on_error = None

    def set_handlers(self, on_delta, on_done, on_error):
        """Register UI callbacks. on_delta(text), on_done(), on_error(msg)."""
        self._on_delta = on_delta
        self._on_done = on_done
        self._on_error = on_error

    async def start(self):
        cfg = SubprocessConfig(
            github_token=self.github_token,
            use_logged_in_user=(self.github_token is None),
        )
        self.client = CopilotClient(cfg, auto_start=False)
        await self.client.start()

        # Surfaces whether Copilot auth succeeded; raised to the UI if not.
        status = await self.client.get_auth_status()

        self.session = await self.client.create_session(
            on_permission_request=_approve_all,
            model=self.model,
            streaming=True,
            on_event=self._handle_event,
        )
        return status

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
            elif t == SessionEventType.SESSION_ERROR:
                msg = getattr(event.data, "message", None) or str(event.data)
                if self._on_error:
                    self._on_error(str(msg))
        except Exception as e:  # never let a handler crash the SDK loop
            if self._on_error:
                self._on_error(f"event handler error: {e}")

    async def send(self, prompt: str):
        if not self.session:
            raise RuntimeError("Session not started -- call start() first.")
        await self.session.send(prompt)

    async def list_models(self):
        if not self.client:
            return []
        return await self.client.list_models()

    async def set_model(self, model: str):
        if self.session:
            await self.session.set_model(model)
            self.model = model

    async def stop(self):
        if self.client:
            await self.client.stop()
