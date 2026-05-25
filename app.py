"""
app.py
======
Desktop shell for Copilot Desktop. Uses pywebview to render index.html in a
native window (WebView2 on Windows 11 -- already present, no admin/install) and
bridges the JavaScript UI to the async Copilot SDK backend.

Run:  python app.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import sys
import threading
import time


def _dbg(*parts):
    """Print startup/diagnostic trace to the console when COPILOT_DEBUG is set.
    Run the app with $env:COPILOT_DEBUG="1" to see exactly where it stalls."""
    if os.environ.get("COPILOT_DEBUG"):
        print(f"[dbg {time.strftime('%H:%M:%S')}]", *parts, file=sys.stderr, flush=True)

# If pywebview falls back to its Qt backend (e.g. on Linux without GTK),
# make sure qtpy targets PyQt6. Harmless on Windows/GTK. Set before importing.
os.environ.setdefault("QT_API", "pyqt6")

# WebView2 can stall its startup on background network calls (component updates,
# SmartScreen, telemetry). These flags skip them so init is fast + consistent and
# doesn't hang on a slow/blocked network. Read by WebView2 at environment creation.
os.environ.setdefault(
    "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
    "--no-first-run --disable-background-networking --disable-component-update "
    "--disable-features=msSmartScreenProtection,OptimizationGuideModelDownloading",
)

_dbg("importing webview/pythonnet ...")
import webview
_dbg("webview imported")

# pywebview's Windows (WinForms) backend logs the native window object at
# startup; pythonnet's repr of its AccessibilityObject recurses infinitely
# ("...Empty.Empty.Empty... maximum recursion depth exceeded"). The app is
# unaffected -- raising the logger level stops that record from being formatted.
logging.getLogger("pywebview").setLevel(logging.CRITICAL)

from terminal import Terminal
# NOTE: copilot_backend (which imports the heavy Copilot SDK) is imported lazily
# inside start(), so the window + "Connecting…" spinner appear immediately instead
# of waiting on the SDK import — especially noticeable on a cold first run.

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(HERE, "index.html")

# Conversations persist here so they survive restarts (kept out of the project
# dir / git, under the user's home).
HISTORY_DIR = os.path.join(os.path.expanduser("~"), ".copilot-desktop")
HISTORY_FILE = os.path.join(HISTORY_DIR, "history.json")
PREFS_FILE = os.path.join(HISTORY_DIR, "prefs.json")  # small app prefs, e.g. last working folder


def _load_env_file() -> None:
    """Load a .env into the process environment so the Copilot SDK subprocess
    inherits things like corporate proxy settings (HTTPS_PROXY / HTTP_PROXY /
    NO_PROXY) even when the app is launched by double-click rather than from a
    shell that already exported them.

    Looks at COPILOT_ENV_FILE (explicit path) first, then a .env next to app.py.
    Existing environment variables are NOT overridden (setdefault semantics), and
    values may be quoted. Robust to passwords containing special characters since
    we split only on the first '='."""
    candidates = []
    explicit = os.environ.get("COPILOT_ENV_FILE")
    if explicit:
        candidates.append(explicit)
    candidates.append(os.path.join(HERE, ".env"))
    for path in candidates:
        try:
            if not (path and os.path.isfile(path)):
                continue
            with open(path, encoding="utf-8") as f:
                for raw in f:
                    line = raw.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    if line.lower().startswith("export "):
                        line = line[7:]
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    if key:
                        os.environ.setdefault(key, val)
        except Exception:
            pass  # never let env loading break startup


def _apply_copilot_proxy() -> None:
    """Mirror run-copilot.ps1: turn the Copilot CLI's COPILOT_PROXY_* settings into
    the standard proxy environment the (Node-based) copilot binary actually reads.

    The CLI binary doesn't read COPILOT_PROXY_* itself — a launcher script builds
    an http://user:pass@host URL and exports HTTP(S)_PROXY plus the NODE_* flags
    that make Node honor the env proxy and trust the corporate root CA. We do the
    same so the bundled/`COPILOT_EXE` subprocess can reach GitHub behind the proxy.

    No-op if no proxy host is configured. Existing HTTP(S)_PROXY is respected."""
    host = os.environ.get("COPILOT_PROXY_HOST")
    if not host:
        return
    if not (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")):
        from urllib.parse import quote
        user = os.environ.get("COPILOT_PROXY_USERNAME", "")
        pwd = os.environ.get("COPILOT_PROXY_PASSWORD", "")
        auth = f"{quote(user, safe='')}:{quote(pwd, safe='')}@" if (user or pwd) else ""
        proxy_url = f"http://{auth}{host}"
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            os.environ[k] = proxy_url
    # Node-based copilot CLI: honor the env proxy and trust the OS/corporate CA.
    os.environ.setdefault("NODE_USE_ENV_PROXY", "1")
    os.environ.setdefault("NODE_USE_SYSTEM_CA", "1")
    # Keep any NO_PROXY bypass list consistent across upper/lower case.
    no_proxy = os.environ.get("NO_PROXY") or os.environ.get("no_proxy")
    if no_proxy:
        os.environ["NO_PROXY"] = no_proxy
        os.environ["no_proxy"] = no_proxy


def _load_prefs() -> dict:
    try:
        with open(PREFS_FILE, encoding="utf-8") as f:
            d = json.load(f)
            if isinstance(d, dict):
                return d
    except Exception:
        pass
    return {}


def _save_prefs(d: dict) -> None:
    os.makedirs(HISTORY_DIR, exist_ok=True)
    tmp = PREFS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PREFS_FILE)  # atomic write


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
        # Integrated terminals (PowerShell on Windows). Multiple tabs, each its own
        # shell; a new tab opens in the active project folder. Keyed by string id.
        self.terminals: dict[str, Terminal] = {}
        self._term_seq = 0
        # UI updates (evaluate_js) are dispatched on a single worker thread so the
        # callers (the asyncio loop, streaming/event callbacks) never block on the
        # WebView. One consumer keeps messages strictly ordered.
        self._js_queue: queue.Queue = queue.Queue()
        threading.Thread(target=self._js_worker, daemon=True).start()
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
        """Queue a global JS call on the UI (non-blocking). The dispatcher thread
        runs evaluate_js so callers never wait on the WebView."""
        if not self.window:
            return
        payload = ",".join(json.dumps(a) for a in args)
        self._js_queue.put((fn, payload))

    def _js_worker(self):
        """Single consumer: run queued evaluate_js calls in order, off the loop thread."""
        while True:
            fn, payload = self._js_queue.get()
            try:
                if self.window:
                    _dbg("_js exec", fn)
                    self.window.evaluate_js(f"window.{fn} && window.{fn}({payload})")
            except Exception as e:
                _dbg("_js error", fn, repr(e))

    def _make_terminal(self) -> str:
        """Create a new terminal in the active project folder; return its id."""
        cwd = (self.backend.working_dir if self.backend else None) or os.path.expanduser("~")
        self._term_seq += 1
        tid = f"t{self._term_seq}"
        t = Terminal(cwd)
        t.set_handlers(
            on_output=lambda text, _id=tid: self._js("onTermOutput", _id, text),
            on_done=lambda code, _id=tid: self._js("onTermDone", _id, code),
        )
        self.terminals[tid] = t
        return tid

    # ----- exposed to the UI -----

    def start(self, github_token: str | None = None):
        # Load the .env + proxy here (not at process start) so a slow OneDrive read
        # of COPILOT_ENV_FILE happens behind the "Connecting…" spinner, not before
        # the window appears. Both are idempotent (setdefault), safe to re-run.
        _dbg("start(): loading env/proxy")
        _load_env_file()
        _apply_copilot_proxy()
        token = github_token or os.environ.get("GITHUB_TOKEN") or None
        # Restore the last working folder the user chose (persisted in prefs), so the
        # app reopens in the same project instead of defaulting to the home folder.
        workdir = (os.environ.get("COPILOT_WORKDIR")
                   or _load_prefs().get("workdir")
                   or os.path.expanduser("~"))
        if not (workdir and os.path.isdir(workdir)):   # saved folder gone? fall back to home
            workdir = os.path.expanduser("~")
        from copilot_backend import CopilotBackend   # lazy: heavy SDK import, kept off the UI startup path
        self.backend = CopilotBackend(github_token=token, working_dir=workdir)
        self.backend.set_handlers(
            on_delta=lambda c: self._js("onCopilotDelta", c),
            on_done=lambda: self._js("onCopilotDone"),
            on_error=lambda m: self._js("onCopilotError", m),
            on_activity=lambda d: self._js("onCopilotActivity", d),
            on_permission=lambda p: self._js("onPermissionRequest", p),
        )
        try:
            _dbg("start(): calling backend.start() ...")
            status = self._run(self.backend.start(), timeout=200)
            _dbg("start(): backend.start() returned; authenticated =", self.backend.authenticated)
            if not self.backend.authenticated:
                return {"ok": False, "needsAuth": True, "error": "Not signed in to GitHub Copilot",
                        "host": os.environ.get("COPILOT_HOST", "")}
            models = []
            try:   # bounded + non-fatal: a slow proxy shouldn't stall startup
                _dbg("start(): list_models() ...")
                models = [getattr(m, "id", str(m)) for m in self._run(self.backend.list_models(), timeout=20)]
                _dbg("start(): list_models() returned", len(models), "models")
            except Exception as e:
                _dbg("start(): list_models() failed/timed out:", repr(e))
            _dbg("start(): returning ok")
            return {"ok": True, "status": str(status), "models": models,
                    "workdir": self.backend.working_dir, "login": self.backend.login}
        except Exception as e:
            _dbg("start(): EXCEPTION:", repr(e))
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

    def new_project(self, name=None):
        """Create a fresh empty project folder under ~/CopilotProjects and switch to it."""
        if not self.backend:
            return {"ok": False, "error": "Backend not started"}
        try:
            base = os.path.join(os.path.expanduser("~"), "CopilotProjects")
            os.makedirs(base, exist_ok=True)
            safe = "".join(c for c in (name or "") if c.isalnum() or c in " -_").strip()
            folder = safe or ("Project-" + time.strftime("%Y%m%d-%H%M%S"))
            path = os.path.join(base, folder)
            n = path
            i = 2
            while os.path.exists(n):   # avoid clobbering an existing folder
                n = f"{path}-{i}"; i += 1
            path = n
            os.makedirs(path)
            self._run(self.backend.set_working_dir(path))
            try:
                prefs = _load_prefs(); prefs["workdir"] = path; _save_prefs(prefs)
            except Exception:
                pass
            return {"ok": True, "path": path}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def set_working_dir(self, path, remember=True):
        # `remember` persists this as the default folder for next launch. We only
        # remember folders the user *explicitly picks* -- browsing an old chat
        # switches the live folder (remember=False) but must not change the default.
        if not self.backend:
            return {"ok": False, "error": "Backend not started"}
        try:
            self._run(self.backend.set_working_dir(path))
            if remember:
                try:
                    prefs = _load_prefs(); prefs["workdir"] = path; _save_prefs(prefs)
                except Exception:
                    pass
            return {"ok": True, "workdir": path}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_config_discovery(self):
        return {"ok": True, "on": bool(self.backend.config_discovery) if self.backend else True}

    def set_config_discovery(self, on):
        if not self.backend:
            return {"ok": False, "error": "Backend not started"}
        try:
            self._run(self.backend.set_config_discovery(bool(on)))
            return {"ok": True, "on": bool(on)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ----- integrated terminals (tabbed) -----

    def term_new(self):
        """Open a new terminal tab in the active project folder."""
        tid = self._make_terminal()
        t = self.terminals[tid]
        return {"ok": True, "id": tid, "cwd": t.cwd, "shell": t.shell_name}

    def term_run(self, tid, command):
        """Run a command in the given tab; output streams via onTermOutput(id, text)."""
        t = self.terminals.get(tid)
        if not t:
            return {"ok": False, "error": "No such terminal"}
        t.run(command)
        return {"ok": True}

    def term_interrupt(self, tid):
        t = self.terminals.get(tid)
        if t:
            t.interrupt()
        return {"ok": True}

    def term_cwd(self, tid):
        t = self.terminals.get(tid)
        return {"ok": bool(t), "cwd": t.cwd if t else ""}

    def term_close(self, tid):
        t = self.terminals.pop(tid, None)
        if t:
            t.interrupt()
        return {"ok": True}

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

    def sign_in(self, host=None):
        """Run the bundled Copilot device-flow login in the background, streaming the
        device code/URL to the UI; on success, reconnect (re-run start). Pass a host
        (e.g. https://your-co.ghe.com) to sign in to GitHub Enterprise Cloud."""
        import threading, subprocess, re
        cli = self._copilot_cli()
        if not cli:
            return {"ok": False, "error": "Copilot CLI not found"}
        host = (host or os.environ.get("COPILOT_HOST") or "").strip()
        args = [cli, "login"]
        if host and host not in ("https://github.com", "github.com"):
            if not host.startswith("http"):
                host = "https://" + host
            args += ["--host", host]

        def worker():
            try:
                p = subprocess.Popen(args, stdout=subprocess.PIPE,
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
        # First call marks when the JS bridge became ready (~pywebviewready). The gap
        # from "main: webview.start()" to here is pure WebView2 init + page load.
        _dbg("api.list_conversations (bridge ready)")
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
    # Optional: `python app.py --host https://your-co.ghe.com` for GitHub Enterprise.
    import sys
    if "--host" in sys.argv:
        i = sys.argv.index("--host")
        if i + 1 < len(sys.argv):
            os.environ["COPILOT_HOST"] = sys.argv[i + 1]
    # env/proxy loading moved into Api.start() so a slow OneDrive .env read happens
    # behind the spinner rather than delaying the window from appearing.
    _dbg("main: creating window")          # gap vs the very first line below = Python import time
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
    # Persist the WebView2 profile/cache in a stable folder instead of pywebview's
    # default private (throwaway) mode. Without this, WebView2 re-initializes its
    # profile every launch, which causes the slow + highly variable cold start
    # (sometimes 3s, sometimes 20s+) before the page/spinner can render.
    wv_data = os.path.join(HISTORY_DIR, "webview2")
    try:
        os.makedirs(wv_data, exist_ok=True)
    except Exception:
        pass
    _dbg("main: webview.start() — gap to 'start(): ...' = WebView2 init + page load")
    # gui=None lets pywebview pick the platform's webview (EdgeWebView2 on Win11).
    try:
        webview.start(private_mode=False, storage_path=wv_data)
    except TypeError:
        webview.start()   # older pywebview without these kwargs


if __name__ == "__main__":
    main()
