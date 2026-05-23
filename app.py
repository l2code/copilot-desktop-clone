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
import logging
import os
import threading

# If pywebview falls back to its Qt backend (e.g. on Linux without GTK),
# make sure qtpy targets PyQt6. Harmless on Windows/GTK. Set before importing.
os.environ.setdefault("QT_API", "pyqt6")

import webview

# pywebview's Windows (WinForms) backend logs the native window object at
# startup; pythonnet's repr of its AccessibilityObject recurses infinitely
# ("...Empty.Empty.Empty... maximum recursion depth exceeded"). The app is
# unaffected -- raising the logger level stops that record from being formatted.
logging.getLogger("pywebview").setLevel(logging.CRITICAL)

from copilot_backend import CopilotBackend

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(HERE, "index.html")

# Conversations persist here so they survive restarts (kept out of the project
# dir / git, under the user's home).
HISTORY_DIR = os.path.join(os.path.expanduser("~"), ".copilot-desktop")
HISTORY_FILE = os.path.join(HISTORY_DIR, "history.json")


def _load_history() -> dict:
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict) and "conversations" in data:
                return data
    except Exception:
        pass
    return {"conversations": []}


def _save_history(data: dict) -> None:
    os.makedirs(HISTORY_DIR, exist_ok=True)
    tmp = HISTORY_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, HISTORY_FILE)  # atomic write


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
        workdir = os.environ.get("COPILOT_WORKDIR") or os.path.expanduser("~")
        self.backend = CopilotBackend(github_token=token, working_dir=workdir)
        self.backend.set_handlers(
            on_delta=lambda c: self._js("onCopilotDelta", c),
            on_done=lambda: self._js("onCopilotDone"),
            on_error=lambda m: self._js("onCopilotError", m),
            on_activity=lambda d: self._js("onCopilotActivity", d),
            on_permission=lambda p: self._js("onPermissionRequest", p),
        )
        try:
            status = self._run(self.backend.start())
            if not self.backend.authenticated:
                return {"ok": False, "needsAuth": True, "error": "Not signed in to GitHub Copilot"}
            models = []
            try:
                models = [getattr(m, "id", str(m)) for m in self._run(self.backend.list_models())]
            except Exception:
                pass
            return {"ok": True, "status": str(status), "models": models,
                    "workdir": self.backend.working_dir, "login": self.backend.login}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def send(self, prompt: str, attachments=None):
        if not self.backend:
            return {"ok": False, "error": "Backend not started"}
        try:
            self._run(self.backend.send(prompt, attachments))
            return {"ok": True}
        except Exception as e:
            self._js("onCopilotError", str(e))
            return {"ok": False, "error": str(e)}

    def abort(self):
        if not self.backend:
            return {"ok": False, "error": "Backend not started"}
        try:
            self._run(self.backend.abort())
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def compact(self):
        if not self.backend:
            return {"ok": False, "error": "Backend not started"}
        try:
            self._run(self.backend.compact())
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_instructions(self):
        return {"ok": True, "text": self.backend.instructions if self.backend else ""}

    def set_instructions(self, text):
        if not self.backend:
            return {"ok": False, "error": "Backend not started"}
        try:
            self._run(self.backend.set_instructions(text))
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_mcp(self):
        return {"ok": True, "servers": (self.backend.mcp_servers or {}) if self.backend else {}}

    def set_mcp(self, servers):
        if not self.backend:
            return {"ok": False, "error": "Backend not started"}
        try:
            self._run(self.backend.set_mcp_servers(servers))
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_mcp_status(self):
        if not self.backend:
            return {"ok": True, "status": {}, "disabled": []}
        return {"ok": True, "status": dict(self.backend.mcp_status),
                "disabled": list(self.backend.mcp_disabled)}

    def set_mcp_enabled(self, name, enabled):
        if not self.backend:
            return {"ok": False, "error": "Backend not started"}
        try:
            self._run(self.backend.set_mcp_enabled(name, bool(enabled)))
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def resolve_permission(self, rid, decision):
        if self.backend:
            self.backend.resolve_permission(rid, decision)
        return {"ok": True}

    def set_auto_approve(self, value):
        if self.backend:
            self.backend.set_auto_approve(value)
        return {"ok": True}

    def read_file(self, path, max_bytes=400000):
        """Read a text file for preview in the side panel."""
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return {"ok": True, "content": f.read(max_bytes)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def pick_folder(self):
        if not self.window:
            return None
        fd = getattr(getattr(webview, "FileDialog", None), "FOLDER", 20)
        res = self.window.create_file_dialog(fd)
        if not res:
            return None
        path = res[0] if isinstance(res, (list, tuple)) else res
        return {"path": path}

    def set_working_dir(self, path):
        if not self.backend:
            return {"ok": False, "error": "Backend not started"}
        try:
            self._run(self.backend.set_working_dir(path))
            return {"ok": True, "workdir": path}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def read_image(self, path, max_bytes=8000000):
        """Read an image file and return it base64-encoded for a BlobAttachment."""
        import base64, mimetypes
        try:
            with open(path, "rb") as f:
                raw = f.read(max_bytes + 1)
            if len(raw) > max_bytes:
                return {"ok": False, "error": "Image too large (over 8 MB)"}
            mime = mimetypes.guess_type(path)[0] or "image/png"
            return {"ok": True, "data": base64.b64encode(raw).decode("ascii"),
                    "mimeType": mime, "name": os.path.basename(path)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def pick_file(self):
        """Open a native file picker and return the chosen file (no admin needed)."""
        if not self.window:
            return None
        dialog_open = getattr(getattr(webview, "FileDialog", None), "OPEN", 10)
        res = self.window.create_file_dialog(dialog_open, allow_multiple=False)
        if not res:
            return None
        path = res[0] if isinstance(res, (list, tuple)) else res
        return {"path": path, "name": os.path.basename(path)}

    def set_model(self, model: str, reasoning: str | None = None):
        if not self.backend:
            return {"ok": False, "error": "Backend not started"}
        try:
            self._run(self.backend.set_model(model, reasoning))
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_commands(self):
        """Copilot slash commands captured from commands.changed events."""
        return self.backend.commands if self.backend else []

    def _copilot_cli(self):
        import copilot
        base = os.path.join(os.path.dirname(copilot.__file__), "bin")
        for name in ("copilot.exe", "copilot"):
            p = os.path.join(base, name)
            if os.path.exists(p):
                return p
        return None

    def sign_in(self):
        """Run the bundled Copilot device-flow login in the background, streaming the
        device code/URL to the UI; on success, reconnect (re-run start)."""
        import threading, subprocess, re
        cli = self._copilot_cli()
        if not cli:
            return {"ok": False, "error": "Copilot CLI not found"}

        def worker():
            try:
                p = subprocess.Popen([cli, "login"], stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, bufsize=1)
                for line in p.stdout:
                    url = re.search(r"https://\S*github\.com/login/device\S*", line)
                    code = re.search(r"\b([A-Z0-9]{4}-[A-Z0-9]{4})\b", line)
                    if url or code:
                        self._js("onAuthCode",
                                 url.group(0) if url else "https://github.com/login/device",
                                 code.group(1) if code else "")
                    if "signed in" in line.lower():
                        self._js("onAuthStatus", line.strip()[:120])
                rc = p.wait(timeout=600)
                if rc == 0:
                    try:
                        if self.backend and self.backend.client:
                            self._run(self.backend.client.stop())
                    except Exception:
                        pass
                    res = self.start()          # re-run start; now authenticated
                    self._js("onAuthDone", res)
                else:
                    self._js("onAuthDone", {"ok": False, "error": "Login exited (code %d)" % rc})
            except Exception as e:
                self._js("onAuthDone", {"ok": False, "error": str(e)})

        threading.Thread(target=worker, daemon=True).start()
        return {"ok": True, "started": True}

    def get_mode(self):
        return {"ok": True, "mode": self.backend.mode if self.backend else "interactive"}

    def set_mode(self, mode):
        if not self.backend:
            return {"ok": False, "error": "Backend not started"}
        try:
            self._run(self.backend.set_mode(mode))
            return {"ok": True, "mode": mode}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def undo(self):
        if not self.backend:
            return {"ok": False, "error": "Backend not started"}
        try:
            return self._run(self.backend.undo())
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_perm_rules(self):
        return {"ok": True, "rules": dict(self.backend.perm_rules) if self.backend else {}}

    def set_perm_rules(self, rules):
        if self.backend:
            self.backend.set_perm_rules(rules)
        return {"ok": True}

    def list_files(self, query=""):
        base = (self.backend.working_dir if self.backend and self.backend.working_dir
                else os.path.expanduser("~"))
        q = (query or "").lower()
        out, scanned = [], 0
        try:
            for root, dirs, files in os.walk(base):
                dirs[:] = [d for d in dirs if not d.startswith(".")][:40]
                for f in files:
                    scanned += 1
                    if scanned > 4000:
                        return {"ok": True, "base": base, "files": out}
                    if f.startswith("."):
                        continue
                    rel = os.path.relpath(os.path.join(root, f), base)
                    if q in rel.lower():
                        out.append(rel.replace(os.sep, "/"))
                        if len(out) >= 25:
                            return {"ok": True, "base": base, "files": out}
        except Exception as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "base": base, "files": out}

    def get_usage(self):
        if not self.backend:
            return {"ok": False, "error": "Backend not started"}
        try:
            return {"ok": True, "quota": self._run(self.backend.get_quota())}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ----- conversation history (no backend required) -----

    def list_conversations(self):
        convs = sorted(
            _load_history()["conversations"],
            key=lambda c: c.get("updated", 0),
            reverse=True,
        )
        return [
            {"id": c["id"], "title": c.get("title", "Untitled"),
             "updated": c.get("updated", 0), "cwd": c.get("cwd")}
            for c in convs
        ]

    def get_conversation(self, conv_id: str):
        for c in _load_history()["conversations"]:
            if c["id"] == conv_id:
                return c
        return None

    def save_conversation(self, conv_id: str, title: str, messages: list):
        import time

        data = _load_history()
        now = time.time()
        cwd = self.backend.working_dir if self.backend else None
        for c in data["conversations"]:
            if c["id"] == conv_id:
                c.update(title=title, messages=messages, updated=now, cwd=cwd)
                break
        else:
            data["conversations"].append(
                {"id": conv_id, "title": title, "messages": messages,
                 "created": now, "updated": now, "cwd": cwd}
            )
        _save_history(data)
        return {"ok": True}

    def delete_conversation(self, conv_id: str):
        data = _load_history()
        data["conversations"] = [c for c in data["conversations"] if c["id"] != conv_id]
        _save_history(data)
        return {"ok": True}

    def clear_history(self):
        _save_history({"conversations": []})
        return {"ok": True}


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
