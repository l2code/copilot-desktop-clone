"""
app.py
======
Desktop shell for the Copilot clone. Uses pywebview to render index.html in a
native window (WebView2 on Windows 11 -- already present, no admin/install) and
bridges the JavaScript UI to the async Copilot SDK backend.

Run:  python app.py
"""

from __future__ import annotations

import asyncio
import json
import os
import threading

# If pywebview falls back to its Qt backend (e.g. on Linux without GTK),
# make sure qtpy targets PyQt6. Harmless on Windows/GTK. Set before importing.
os.environ.setdefault("QT_API", "pyqt6")

import webview

from copilot_backend import CopilotBackend

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(HERE, "index.html")


class Api:
    """Methods on this object are callable from JS as window.pywebview.api.*"""

    def __init__(self):
        self.window = None
        self.backend: CopilotBackend | None = None
        # The SDK is async; run one event loop in a background thread and
        # marshal coroutines onto it from the (sync) JS-facing methods.
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self._run_loop, daemon=True).start()

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def _run(self, coro, timeout=180):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout=timeout)

    def _js(self, fn, *args):
        """Invoke a global JS function on the UI, passing JSON-safe args."""
        if not self.window:
            return
        payload = ",".join(json.dumps(a) for a in args)
        self.window.evaluate_js(f"window.{fn} && window.{fn}({payload})")

    # ----- exposed to the UI -----

    def start(self, github_token: str | None = None):
        token = github_token or os.environ.get("GITHUB_TOKEN") or None
        self.backend = CopilotBackend(github_token=token)
        self.backend.set_handlers(
            on_delta=lambda c: self._js("onCopilotDelta", c),
            on_done=lambda: self._js("onCopilotDone"),
            on_error=lambda m: self._js("onCopilotError", m),
        )
        try:
            status = self._run(self.backend.start())
            models = []
            try:
                models = [getattr(m, "id", str(m)) for m in self._run(self.backend.list_models())]
            except Exception:
                pass
            return {"ok": True, "status": str(status), "models": models}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def send(self, prompt: str):
        if not self.backend:
            return {"ok": False, "error": "Backend not started"}
        try:
            self._run(self.backend.send(prompt))
            return {"ok": True}
        except Exception as e:
            self._js("onCopilotError", str(e))
            return {"ok": False, "error": str(e)}

    def set_model(self, model: str):
        if not self.backend:
            return {"ok": False, "error": "Backend not started"}
        try:
            self._run(self.backend.set_model(model))
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}


def main():
    api = Api()
    window = webview.create_window(
        "Copilot Desktop",
        INDEX,
        js_api=api,
        width=1100,
        height=760,
        min_size=(820, 600),
    )
    api.window = window
    # gui=None lets pywebview pick the platform's webview (EdgeWebView2 on Win11).
    webview.start()


if __name__ == "__main__":
    main()
