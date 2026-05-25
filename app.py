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
    # --no-proxy-server: the UI is a local page, so the WebView never needs the
    # network. Behind a corporate proxy its background probes otherwise hang on the
    # proxy (~25s TCP timeout, sometimes indefinite); going direct makes them fail
    # fast. (Copilot traffic is unaffected — it runs in a separate process that uses
    # the proxy env vars.)
    # --disable-gpu / --disable-gpu-sandbox: in a virtualized/published session
    # (Citrix, RDP, VMs) there's no real GPU, and WebView2's GPU/compositor init can
    # hang the window ("Not Responding") and never load the page. Software rendering
    # is reliable there. Harmless on real hardware for a simple UI like this.
    "--disable-gpu --disable-gpu-compositing --disable-gpu-sandbox "
    # --disk-cache-size=1 effectively disables the asset cache so an updated
    # styles.css/js always loads (otherwise the persistent profile can serve a
    # stale cached copy after you update the app). Profile/login still persist.
    "--disk-cache-size=1 --no-proxy-server --no-first-run --no-default-browser-check "
    "--disable-background-networking --disable-component-update --disable-sync "
    "--disable-domain-reliability --disable-client-side-phishing-detection --disable-breakpad "
    "--disable-features=msSmartScreenProtection,OptimizationGuideModelDownloading,"
    "OptimizationHints,Translate,InterestFeedContentSuggestions,MediaRouter",
)

_dbg("importing webview/pythonnet ...")
import webview
_dbg("webview imported")

# pywebview's Windows (WinForms) backend logs the native window object at
# startup; pythonnet's repr of its AccessibilityObject recurses infinitely
# ("...Empty.Empty.Empty... maximum recursion depth exceeded"). The app is
# unaffected -- raising the logger level stops that record from being formatted.
logging.getLogger("pywebview").setLevel(logging.CRITICAL)

from activity import ActivityLog
from automation_service import AutomationService
from file_service import FileService
from github_service import GitHubService
from gitlab_service import GitLabService
from mcp_gitlab_service import GitLabMCPService
import git_service
from project_service import ProjectService
from session_manager import SessionManager
from settings_service import SettingsService
from storage import Storage
from terminal import Terminal
from troubleshooting_service import TroubleshootingService
from workflow_service import WorkflowService
from workspace_service import WorkspaceService
# NOTE: copilot_backend (which imports the heavy Copilot SDK) is imported lazily
# inside start(), so the window + "Connecting…" spinner appear immediately instead
# of waiting on the SDK import — especially noticeable on a cold first run.

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(HERE, "index.html")

# Conversations persist here so they survive restarts (kept out of the project
# dir / git, under the user's home).
HISTORY_DIR = os.environ.get("COPILOT_DESKTOP_HOME") or os.path.join(os.path.expanduser("~"), ".copilot-desktop")
HISTORY_FILE = os.path.join(HISTORY_DIR, "history.json")
PREFS_FILE = os.path.join(HISTORY_DIR, "prefs.json")  # small app prefs, e.g. last working folder
_ENV_FILE_STATUS = {
    "explicit": None,
    "explicit_exists": False,
    "loaded_path": None,
    "loaded_keys": [],
    "gitlab_keys": [],
}


def _norm_env_path(path: str | None) -> str | None:
    if not path:
        return None
    return os.path.abspath(os.path.expandvars(os.path.expanduser(path)))


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _load_env_file() -> None:
    """Load a .env into the process environment so the Copilot SDK subprocess
    inherits things like corporate proxy settings (HTTPS_PROXY / HTTP_PROXY /
    NO_PROXY) even when the app is launched by double-click rather than from a
    shell that already exported them.

    Looks at COPILOT_ENV_FILE (explicit path) first, then a .env next to app.py.
    Existing environment variables are NOT overridden (setdefault semantics), and
    values may be quoted. Robust to passwords containing special characters since
    we split only on the first '='."""
    explicit_keys = {
        "GITLAB_URL", "GITLAB_API_URL", "GITLAB_PROJECT_ID", "GITLAB_PROJECT_PATH",
        "GITLAB_GROUP_ID", "GITLAB_GROUP_PATH", "GITLAB_TOKEN",
        "GITLAB_PERSONAL_ACCESS_TOKEN", "GL_TOKEN", "GITLAB_PRIVATE_TOKEN",
    }
    global _ENV_FILE_STATUS
    candidates = []
    explicit = os.environ.get("COPILOT_ENV_FILE")
    if not explicit and os.name == "nt":
        # `setx COPILOT_ENV_FILE ...` updates the user's registry environment but
        # not the already-open PowerShell process. Reading HKCU makes the very next
        # app launch in that same terminal see the value, which matches how native
        # desktop apps tend to pick up user-level environment changes.
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                explicit = winreg.QueryValueEx(key, "COPILOT_ENV_FILE")[0]
        except Exception:
            explicit = None
    if explicit:
        candidates.append(explicit)
    candidates.append(os.path.join(HERE, ".env"))
    explicit_abs = _norm_env_path(explicit)
    loaded_path = None
    loaded_keys: set[str] = set()
    for path in candidates:
        try:
            path_abs = _norm_env_path(path)
            if not (path_abs and os.path.isfile(path_abs)):
                continue
            with open(path_abs, encoding="utf-8-sig") as f:
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
                        loaded_path = path_abs
                        loaded_keys.add(key)
                        if (explicit_abs and os.path.normcase(path_abs) == os.path.normcase(explicit_abs)
                                and key in explicit_keys):
                            os.environ[key] = val
                        else:
                            os.environ.setdefault(key, val)
        except Exception:
            pass  # never let env loading break startup
    _ENV_FILE_STATUS = {
        "explicit": explicit,
        "explicit_exists": bool(explicit_abs and os.path.isfile(explicit_abs)),
        "loaded_path": loaded_path,
        "loaded_keys": sorted(loaded_keys),
        "gitlab_keys": sorted(k for k in loaded_keys if k.startswith("GITLAB_") or k == "GL_TOKEN"),
    }


def _env_file_status() -> dict:
    return dict(_ENV_FILE_STATUS)


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
        _load_env_file()
        _apply_copilot_proxy()
        self.storage = Storage()
        self.activity = ActivityLog(self.storage)
        self.projects = ProjectService(self.storage)
        self.workspaces = WorkspaceService(self.storage)
        self.sessions = SessionManager(self.storage, self.projects, self.workspaces, self.activity)
        self.settings = SettingsService(self.storage)
        self.files = FileService()
        self.github = GitHubService()
        self.gitlab = GitLabService(settings_getter=self.settings.get_settings)
        self.gitlab_mcp = GitLabMCPService(settings_getter=self.settings.get_settings)
        self.troubleshooting = TroubleshootingService(self.storage)
        self.automations = AutomationService(self.storage)
        self.workflows = WorkflowService(self.storage, self.activity)
        self.active_project_id: str | None = None
        self.active_workspace_id: str | None = None
        self.active_session_id: str | None = None
        self._start_lock = threading.Lock()
        self._start_inflight = False
        self._last_start_result = None
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

    def _activate_path(self, path: str):
        """Ensure path has project/workspace records and mark it as active."""
        workspace = self.sessions.ensure_workspace_for_path(path)
        self.active_project_id = workspace["project_id"]
        self.active_workspace_id = workspace["id"]
        return workspace

    def _active_workspace(self):
        if self.active_workspace_id:
            ws = self.workspaces.get_workspace(self.active_workspace_id)
            if ws:
                return ws
        path = (self.backend.working_dir if self.backend else None) or _load_prefs().get("workdir") or os.path.expanduser("~")
        return self._activate_path(path)

    def _handle_copilot_done(self):
        if self.active_session_id:
            try:
                session = self.sessions.get_session(self.active_session_id)
                self.sessions.set_running(self.active_session_id, False)
                if session:
                    self.activity.add(
                        "chat",
                        "Assistant response completed",
                        session.get("title"),
                        workspace_id=session["workspace_id"],
                        session_id=self.active_session_id,
                    )
            except Exception:
                pass
        self._js("onCopilotDone")

    def _handle_copilot_error(self, message):
        if self.active_session_id:
            try:
                self.sessions.set_running(self.active_session_id, False, interrupted=True, reason=str(message))
            except Exception:
                pass
        self._js("onCopilotError", message)

    # ----- exposed to the UI -----

    def _start_blocking(self, github_token: str | None = None):
        # Load the .env + proxy here (not at process start) so a slow OneDrive read
        # of COPILOT_ENV_FILE happens behind the "Connecting…" spinner, not before
        # the window appears. Both are idempotent (setdefault), safe to re-run.
        _dbg("_start_blocking(): loading env/proxy")
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
        try:
            self._activate_path(workdir)
            self.sessions.migrate_legacy_history(HISTORY_FILE, workdir)
        except Exception as e:
            _dbg("start(): storage/session bootstrap failed:", repr(e))
        from copilot_backend import CopilotBackend   # lazy: heavy SDK import, kept off the UI startup path
        self.backend = CopilotBackend(github_token=token, working_dir=workdir)
        self.backend.set_handlers(
            on_delta=lambda c: self._js("onCopilotDelta", c),
            on_done=self._handle_copilot_done,
            on_error=self._handle_copilot_error,
            on_activity=lambda d: self._js("onCopilotActivity", d),
            on_permission=lambda p: self._js("onPermissionRequest", p),
        )
        try:
            _dbg("_start_blocking(): calling backend.start() ...")
            status = self._run(self.backend.start(), timeout=200)
            _dbg("_start_blocking(): backend.start() returned; authenticated =", self.backend.authenticated)
            if not self.backend.authenticated:
                self._last_start_result = {"ok": False, "needsAuth": True, "error": "Not signed in to GitHub Copilot",
                        "host": os.environ.get("COPILOT_HOST", "")}
                return self._last_start_result
            models = []
            try:   # bounded + non-fatal: a slow proxy shouldn't stall startup
                _dbg("_start_blocking(): list_models() ...")
                models = [getattr(m, "id", str(m)) for m in self._run(self.backend.list_models(), timeout=20)]
                _dbg("_start_blocking(): list_models() returned", len(models), "models")
            except Exception as e:
                _dbg("_start_blocking(): list_models() failed/timed out:", repr(e))
            _dbg("_start_blocking(): returning ok")
            self._last_start_result = {"ok": True, "status": str(status), "models": models,
                    "workdir": self.backend.working_dir, "login": self.backend.login}
            return self._last_start_result
        except Exception as e:
            _dbg("_start_blocking(): EXCEPTION:", repr(e))
            self._last_start_result = {"ok": False, "error": str(e)}
            return self._last_start_result

    def _start_worker(self, github_token=None):
        try:
            res = self._start_blocking(github_token)
        finally:
            with self._start_lock:
                self._start_inflight = False
        self._js("onBackendReady", res)

    def start(self, github_token: str | None = None):
        """Kick off Copilot connection without blocking the WebView bridge.

        GitHub's desktop app keeps the shell responsive while auth/session setup
        happens in the background. Returning immediately here avoids Windows
        marking the WebView "Not Responding" during proxy/auth/model discovery.
        """
        if self.backend and self.backend.authenticated and self._last_start_result:
            return {**self._last_start_result, "ready": True}
        with self._start_lock:
            if self._start_inflight:
                return {"ok": True, "starting": True}
            self._start_inflight = True
        threading.Thread(target=self._start_worker, args=(github_token,), daemon=True).start()
        return {"ok": True, "starting": True}

    def get_startup_options(self):
        return {
            "ok": True,
            "skip_copilot_start": _env_flag("COPILOT_SKIP_START"),
            "webview_gui": os.environ.get("COPILOT_WEBVIEW_GUI") or "",
            "webview_private": _env_flag("COPILOT_WEBVIEW_PRIVATE"),
            "webview_persist": _env_flag("COPILOT_WEBVIEW_PERSIST", os.name == "nt"),
        }

    def send(self, prompt: str, attachments=None, session_id=None):
        if not self.backend:
            return {"ok": False, "error": "Backend not started"}
        try:
            if session_id:
                try:
                    session = self.sessions.get_session(session_id)
                    if not session:
                        workspace = self._active_workspace()
                        self.sessions.create_session(workspace["id"], "chat", (prompt or "New chat")[:60], session_id=session_id)
                        session = self.sessions.get_session(session_id)
                    if session:
                        self.active_session_id = session_id
                        self.sessions.set_running(session_id, True)
                        self.activity.add(
                            "chat",
                            "User message",
                            (prompt or "")[:120],
                            workspace_id=session["workspace_id"],
                            session_id=session_id,
                        )
                except Exception:
                    pass
            self._run(self.backend.send(prompt, attachments))
            return {"ok": True}
        except Exception as e:
            if session_id:
                try:
                    self.sessions.set_running(session_id, False, interrupted=True, reason=str(e))
                except Exception:
                    pass
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

    def discover_mcp(self):
        if not self.backend:
            return {"ok": True, "servers": []}
        try:
            return {"ok": True, "servers": self._run(self.backend.discover_mcp(), timeout=20)}
        except Exception as e:
            return {"ok": False, "error": str(e), "servers": []}

    def set_discovered_mcp_enabled(self, name, enabled):
        if not self.backend:
            return {"ok": False, "error": "Backend not started"}
        try:
            ok = self._run(self.backend.set_discovered_mcp_enabled(name, bool(enabled)), timeout=60)
            return {"ok": bool(ok)}
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
            workspace = self._activate_path(path)
            self._run(self.backend.set_working_dir(path))
            try:
                prefs = _load_prefs(); prefs["workdir"] = path; _save_prefs(prefs)
            except Exception:
                pass
            self.activity.add(
                "project",
                "Created project",
                path,
                project_id=workspace["project_id"],
                workspace_id=workspace["id"],
            )
            return {"ok": True, "path": path, "project_id": workspace["project_id"], "workspace_id": workspace["id"]}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def set_working_dir(self, path, remember=True):
        # `remember` persists this as the default folder for next launch. We only
        # remember folders the user *explicitly picks* -- browsing an old chat
        # switches the live folder (remember=False) but must not change the default.
        if not self.backend:
            return {"ok": False, "error": "Backend not started"}
        try:
            workspace = self._activate_path(path)
            self._run(self.backend.set_working_dir(path))
            if remember:
                try:
                    prefs = _load_prefs(); prefs["workdir"] = path; _save_prefs(prefs)
                except Exception:
                    pass
            self.activity.add(
                "workspace",
                "Switched workspace",
                path,
                project_id=workspace["project_id"],
                workspace_id=workspace["id"],
            )
            return {"ok": True, "workdir": path, "project_id": workspace["project_id"], "workspace_id": workspace["id"]}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ----- project/workspace/session APIs (new model, old UI still compatible) -----

    def list_projects(self):
        return {"ok": True, "projects": self.projects.list_projects()}

    def create_project(self, path):
        try:
            project = self.projects.create_project(path)
            workspace = self.workspaces.ensure_folder_workspace(project["id"], project["main_repo_path"])
            self.active_project_id = project["id"]
            self.active_workspace_id = workspace["id"]
            self.activity.add(
                "project",
                "Added project",
                project["main_repo_path"],
                project_id=project["id"],
                workspace_id=workspace["id"],
            )
            return {"ok": True, "project": project, "workspace": workspace}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_project(self, project_id):
        project = self.projects.get_project(project_id)
        return {"ok": bool(project), "project": project}

    def archive_project(self, project_id):
        try:
            return self.projects.archive_project(project_id)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def list_workspaces(self, project_id=None):
        try:
            project_id = project_id or self.active_project_id
            if not project_id:
                return {"ok": True, "workspaces": []}
            return {"ok": True, "workspaces": self.workspaces.list_workspaces(project_id)}
        except Exception as e:
            return {"ok": False, "error": str(e), "workspaces": []}

    def create_workspace(self, project_id=None, mode="folder", options=None):
        try:
            options = options or {}
            project_id = project_id or self.active_project_id
            if not project_id:
                raise ValueError("No project selected")
            project = self.projects.get_project(project_id)
            if not project:
                raise ValueError("Project not found")
            path = options.get("path") or project["main_repo_path"]
            workspace = self.workspaces.create_workspace(
                project_id,
                mode or "folder",
                path=path,
                name=options.get("name"),
                branch=options.get("branch"),
                base_branch=options.get("base_branch"),
                source_issue_number=options.get("source_issue_number"),
                source_pr_number=options.get("source_pr_number"),
                metadata=options.get("metadata"),
            )
            self.active_project_id = project_id
            self.active_workspace_id = workspace["id"]
            self.activity.add(
                "workspace",
                "Created workspace",
                workspace["name"],
                project_id=project_id,
                workspace_id=workspace["id"],
            )
            return {"ok": True, "workspace": workspace}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_workspace(self, workspace_id=None):
        workspace = self.workspaces.get_workspace(workspace_id or self.active_workspace_id)
        return {"ok": bool(workspace), "workspace": workspace}

    def set_active_workspace(self, workspace_id):
        try:
            workspace = self.workspaces.get_workspace(workspace_id)
            if not workspace:
                return {"ok": False, "error": "Workspace not found"}
            self.active_project_id = workspace["project_id"]
            self.active_workspace_id = workspace["id"]
            if self.backend:
                self._run(self.backend.set_working_dir(workspace["path"]))
            return {"ok": True, "workspace": workspace}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def list_sessions(self, workspace_id=None):
        try:
            workspace_id = workspace_id or self.active_workspace_id
            if not workspace_id:
                return {"ok": True, "sessions": []}
            return {"ok": True, "sessions": self.sessions.list_sessions(workspace_id)}
        except Exception as e:
            return {"ok": False, "error": str(e), "sessions": []}

    def create_session(self, workspace_id=None, session_type="chat", title=None, source=None):
        try:
            workspace_id = workspace_id or self.active_workspace_id or self._active_workspace()["id"]
            session = self.sessions.create_session(
                workspace_id,
                session_type or "chat",
                title or "New chat",
                metadata={"source": source} if source else {},
            )
            return {"ok": True, "session": session}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_session(self, session_id):
        session = self.sessions.get_session(session_id)
        return {"ok": bool(session), "session": session,
                "messages": self.sessions.get_messages(session_id) if session else []}

    def archive_session(self, session_id):
        try:
            return self.sessions.archive_session(session_id)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def list_activity(self, workspace_id=None):
        try:
            workspace_id = workspace_id or self.active_workspace_id
            return {"ok": True, "activity": self.activity.list_workspace(workspace_id) if workspace_id else []}
        except Exception as e:
            return {"ok": False, "error": str(e), "activity": []}

    def list_project_activity(self, project_id=None):
        try:
            project_id = project_id or self.active_project_id
            return {"ok": True, "activity": self.activity.list_project(project_id) if project_id else []}
        except Exception as e:
            return {"ok": False, "error": str(e), "activity": []}

    def get_git_status(self, workspace_id=None):
        try:
            workspace = self.workspaces.get_workspace(workspace_id or self.active_workspace_id)
            if not workspace:
                return {"ok": False, "error": "Workspace not found"}
            return git_service.get_status(workspace["path"])
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _workspace_for_api(self, workspace_id=None):
        workspace = self.workspaces.get_workspace(workspace_id or self.active_workspace_id)
        if not workspace:
            raise ValueError("Workspace not found")
        return workspace

    def _project_for_api(self, project_id=None):
        project = self.projects.get_project(project_id or self.active_project_id)
        if not project:
            raise ValueError("Project not found")
        return project

    def _repo_context_for_project(self, project_id=None):
        project = self._project_for_api(project_id)
        owner = project.get("github_owner")
        repo = project.get("github_repo")
        if not (owner and repo):
            desc = git_service.describe_repository(project["main_repo_path"])
            owner, repo = desc.get("owner"), desc.get("repo")
        if not (owner and repo):
            raise ValueError("Project is not connected to a GitHub repository")
        return project, owner, repo

    def _gitlab_project_target(self, target=None, project_id=None):
        if target:
            return str(target)
        default_target = self.gitlab.default_project()
        if default_target:
            return default_target
        project = self._project_for_api(project_id)
        remote = git_service.get_remote_url(project["main_repo_path"])
        parsed = git_service.parse_gitlab_remote_url(remote, self.gitlab.host)
        if parsed:
            return parsed
        raise ValueError("Set a GitLab project path/id or use a GitLab remote.")

    def get_changed_files(self, workspace_id=None):
        try:
            workspace = self._workspace_for_api(workspace_id)
            return git_service.get_changed_files(workspace["path"])
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_file_diff(self, workspace_id=None, path=None, staged=False):
        try:
            workspace = self._workspace_for_api(workspace_id)
            return git_service.get_file_diff(workspace["path"], path, staged)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def stage_file(self, workspace_id=None, path=None):
        try:
            workspace = self._workspace_for_api(workspace_id)
            res = git_service.stage_file(workspace["path"], path)
            self.activity.add("git", "Staged file", path, project_id=workspace["project_id"], workspace_id=workspace["id"])
            return res
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def unstage_file(self, workspace_id=None, path=None):
        try:
            workspace = self._workspace_for_api(workspace_id)
            res = git_service.unstage_file(workspace["path"], path)
            self.activity.add("git", "Unstaged file", path, project_id=workspace["project_id"], workspace_id=workspace["id"])
            return res
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def stage_all(self, workspace_id=None):
        try:
            workspace = self._workspace_for_api(workspace_id)
            res = git_service.stage_all(workspace["path"])
            self.activity.add("git", "Staged all changes", None, project_id=workspace["project_id"], workspace_id=workspace["id"])
            return res
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def unstage_all(self, workspace_id=None):
        try:
            workspace = self._workspace_for_api(workspace_id)
            res = git_service.unstage_all(workspace["path"])
            self.activity.add("git", "Unstaged all changes", None, project_id=workspace["project_id"], workspace_id=workspace["id"])
            return res
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def discard_file(self, workspace_id=None, path=None):
        try:
            workspace = self._workspace_for_api(workspace_id)
            res = git_service.discard_file(workspace["path"], path)
            self.activity.add("git", "Discarded file changes", path, project_id=workspace["project_id"], workspace_id=workspace["id"])
            return res
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def commit(self, workspace_id=None, summary="", description=None):
        try:
            workspace = self._workspace_for_api(workspace_id)
            res = git_service.commit(workspace["path"], summary, description)
            if res.get("ok"):
                self.activity.add("git", "Created commit", summary, project_id=workspace["project_id"], workspace_id=workspace["id"])
            return res
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def list_branches(self, workspace_id=None):
        try:
            workspace = self._workspace_for_api(workspace_id)
            return git_service.list_branches(workspace["path"])
        except Exception as e:
            return {"ok": False, "error": str(e), "branches": []}

    def create_branch(self, workspace_id=None, branch_name=None, base_branch=None, checkout=True):
        try:
            workspace = self._workspace_for_api(workspace_id)
            res = git_service.create_branch(workspace["path"], branch_name, base_branch, checkout)
            if res.get("ok"):
                self.activity.add("git", "Created branch", branch_name, project_id=workspace["project_id"], workspace_id=workspace["id"])
            return res
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def checkout_branch(self, workspace_id=None, branch_name=None):
        try:
            workspace = self._workspace_for_api(workspace_id)
            res = git_service.checkout_branch(workspace["path"], branch_name)
            if res.get("ok"):
                self.activity.add("git", "Checked out branch", branch_name, project_id=workspace["project_id"], workspace_id=workspace["id"])
            return res
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def rename_branch(self, workspace_id=None, old_name=None, new_name=None):
        try:
            workspace = self._workspace_for_api(workspace_id)
            return git_service.rename_branch(workspace["path"], old_name, new_name)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def delete_branch(self, workspace_id=None, branch_name=None, force=False):
        try:
            workspace = self._workspace_for_api(workspace_id)
            return git_service.delete_branch(workspace["path"], branch_name, force)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def fetch(self, workspace_id=None):
        try:
            workspace = self._workspace_for_api(workspace_id)
            res = git_service.fetch(workspace["path"])
            self.activity.add("git", "Fetched repository", None, project_id=workspace["project_id"], workspace_id=workspace["id"])
            return res
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def pull(self, workspace_id=None):
        try:
            workspace = self._workspace_for_api(workspace_id)
            res = git_service.pull(workspace["path"])
            self.activity.add("git", "Pulled repository", None, project_id=workspace["project_id"], workspace_id=workspace["id"])
            return res
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def push(self, workspace_id=None):
        try:
            workspace = self._workspace_for_api(workspace_id)
            res = git_service.push(workspace["path"])
            self.activity.add("git", "Pushed repository", None, project_id=workspace["project_id"], workspace_id=workspace["id"])
            return res
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def sync(self, workspace_id=None):
        pulled = self.pull(workspace_id)
        if not pulled.get("ok"):
            return pulled
        return self.push(workspace_id)

    def get_commit_history(self, workspace_id=None, limit=50):
        try:
            workspace = self._workspace_for_api(workspace_id)
            return git_service.get_commit_history(workspace["path"], limit)
        except Exception as e:
            return {"ok": False, "error": str(e), "commits": []}

    def get_commit_details(self, workspace_id=None, sha=None):
        try:
            workspace = self._workspace_for_api(workspace_id)
            return git_service.get_commit_details(workspace["path"], sha)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def create_worktree(self, project_id=None, branch_name=None, base_branch=None, path=None):
        try:
            project = self._project_for_api(project_id)
            if not path:
                base = os.path.dirname(project["main_repo_path"])
                path = os.path.join(base, branch_name or ("worktree-" + time.strftime("%Y%m%d-%H%M%S")))
            res = git_service.create_worktree(project["main_repo_path"], path, branch_name, base_branch)
            if res.get("ok"):
                workspace = self.workspaces.create_workspace(
                    project["id"],
                    "worktree",
                    path=res["path"],
                    branch=branch_name,
                    base_branch=base_branch,
                )
                self.activity.add("workspace", "Created worktree workspace", branch_name, project_id=project["id"], workspace_id=workspace["id"])
                res["workspace"] = workspace
            return res
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def list_workspace_files(self, workspace_id=None):
        try:
            workspace = self._workspace_for_api(workspace_id)
            return self.files.list_tree(workspace["path"])
        except Exception as e:
            return {"ok": False, "error": str(e), "files": []}

    def search_workspace_files(self, workspace_id=None, query=""):
        try:
            workspace = self._workspace_for_api(workspace_id)
            return self.files.search(workspace["path"], query)
        except Exception as e:
            return {"ok": False, "error": str(e), "files": []}

    def read_workspace_file(self, workspace_id=None, path=None, max_bytes=400000):
        try:
            workspace = self._workspace_for_api(workspace_id)
            return self.files.read_file(workspace["path"], path, max_bytes)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_github_auth_status(self):
        return self.github.auth_status()

    def get_repo_context(self, project_id=None):
        try:
            project, owner, repo = self._repo_context_for_project(project_id)
            meta = self.github.get_repo(owner, repo)
            return {"ok": True, "project": project, "owner": owner, "repo": repo, "github": meta}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def list_issues(self, project_id=None):
        try:
            _, owner, repo = self._repo_context_for_project(project_id)
            return self.github.list_issues(owner, repo)
        except Exception as e:
            return {"ok": False, "error": str(e), "issues": []}

    def list_pull_requests(self, project_id=None):
        try:
            _, owner, repo = self._repo_context_for_project(project_id)
            return self.github.list_pull_requests(owner, repo)
        except Exception as e:
            return {"ok": False, "error": str(e), "pull_requests": []}

    def get_pull_request(self, project_id=None, number=None):
        try:
            _, owner, repo = self._repo_context_for_project(project_id)
            return self.github.get_pull_request(owner, repo, number)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def create_pull_request(self, workspace_id=None, title="", body="", base=None, head=None):
        try:
            workspace = self._workspace_for_api(workspace_id)
            project, owner, repo = self._repo_context_for_project(workspace["project_id"])
            base = base or project.get("default_branch") or workspace.get("base_branch") or "main"
            head = head or git_service.get_current_branch(workspace["path"])
            res = self.github.create_pull_request(owner, repo, title, body, base, head)
            if res.get("ok"):
                self.activity.add("github", "Created pull request", title, project_id=project["id"], workspace_id=workspace["id"])
            return res
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def open_issue_session(self, project_id=None, number=None):
        try:
            project = self._project_for_api(project_id)
            workspace = self.workspaces.ensure_folder_workspace(project["id"], project["main_repo_path"])
            session = self.sessions.create_session(workspace["id"], "issue", f"Issue #{number}", metadata={"issue_number": number})
            return {"ok": True, "session": session, "workspace": workspace}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def open_pr_session(self, project_id=None, number=None):
        try:
            project = self._project_for_api(project_id)
            workspace = self.workspaces.ensure_folder_workspace(project["id"], project["main_repo_path"])
            session = self.sessions.create_session(workspace["id"], "pull_request", f"PR #{number}", metadata={"pull_request_number": number})
            return {"ok": True, "session": session, "workspace": workspace}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_gitlab_auth_status(self):
        return self.gitlab.auth_status()

    def get_gitlab_env_status(self):
        status = self.gitlab.env_status()
        status["env_file"] = _env_file_status()
        status["data_source"] = self.settings.get_gitlab_settings().get("data_source", "rest")
        return status

    def get_gitlab_settings(self):
        settings = self.settings.get_gitlab_settings()
        status = self.gitlab.env_status()
        return {"ok": True, "settings": settings, "status": status}

    def update_gitlab_settings(self, patch=None):
        try:
            settings = self.settings.update_gitlab_settings(patch or {})
            status = self.gitlab.env_status()
            return {"ok": True, "settings": settings, "status": status}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_gitlab_mcp_status(self):
        return self.gitlab_mcp.status()

    def get_gitlab_project(self, target=None):
        try:
            target = self._gitlab_project_target(target)
            res = self.gitlab.get_project(target)
            return {"ok": res.get("ok"), "project_target": target, **res}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def list_gitlab_backlog(self, target=None, scope="project", state="opened", labels=None, search=None):
        try:
            if self.settings.get_gitlab_settings().get("data_source") == "mcp":
                return self.gitlab_mcp.search_stories(search or "", target, scope, state or "opened", labels, None)
            if scope == "group":
                group = target or self.gitlab.default_group()
                if not group:
                    raise ValueError("GitLab group path/id is required")
                res = self.gitlab.list_group_issues(group, state=state or "opened", labels=labels, search=search)
                return {"ok": res.get("ok"), "scope": "group", "target": group, **res}
            project = self._gitlab_project_target(target)
            res = self.gitlab.list_project_issues(project, state=state or "opened", labels=labels, search=search)
            return {"ok": res.get("ok"), "scope": "project", "target": project, **res}
        except Exception as e:
            return {"ok": False, "error": str(e), "issues": []}

    def list_gitlab_current_sprint(self, group=None, assignee=None, labels=None):
        try:
            if self.settings.get_gitlab_settings().get("data_source") == "mcp":
                return self.gitlab_mcp.current_sprint(group=group, assignee=assignee, labels=labels)
            group = group or self.gitlab.default_group()
            if not group:
                raise ValueError("Set a GitLab group id/path to load the current sprint iteration.")
            iterations = self.gitlab.list_group_iterations(group, state="current")
            if not iterations.get("ok"):
                return {"ok": False, "group": group, "error": iterations.get("error"), "issues": [], "iterations": []}
            current = (iterations.get("iterations") or [None])[0]
            if not current:
                return {"ok": True, "group": group, "iteration": None, "issues": [], "iterations": [], "message": "No current GitLab iteration found for this group."}
            res = self.gitlab.list_group_issues(
                group,
                state="opened",
                labels=labels,
                iteration_id=current.get("id"),
                assignee_username=assignee,
                per_page=100,
            )
            return {"ok": res.get("ok"), "group": group, "iteration": current, "issues": res.get("issues", []), "error": res.get("error")}
        except Exception as e:
            return {"ok": False, "error": str(e), "issues": []}

    def search_gitlab_stories(self, query="", target=None, scope="project", state="opened", labels=None, assignee=None):
        try:
            if self.settings.get_gitlab_settings().get("data_source") == "mcp":
                return self.gitlab_mcp.search_stories(query, target, scope, state, labels, assignee)
            if scope == "group":
                group = target or self.gitlab.default_group()
                if not group:
                    raise ValueError("GitLab group path/id is required")
                res = self.gitlab.list_group_issues(
                    group,
                    state=state or "opened",
                    labels=labels,
                    search=query,
                    assignee_username=assignee,
                    per_page=100,
                )
                return {"ok": res.get("ok"), "scope": "group", "target": group, "issues": res.get("issues", []), "error": res.get("error")}
            project = self._gitlab_project_target(target)
            res = self.gitlab.list_project_issues(
                project,
                state=state or "opened",
                labels=labels,
                search=query,
                assignee_username=assignee,
                per_page=100,
            )
            return {"ok": res.get("ok"), "scope": "project", "target": project, "issues": res.get("issues", []), "error": res.get("error")}
        except Exception as e:
            return {"ok": False, "error": str(e), "issues": []}

    def get_gitlab_issue(self, target=None, issue_iid=None):
        try:
            if self.settings.get_gitlab_settings().get("data_source") == "mcp":
                return self.gitlab_mcp.get_issue(target, issue_iid)
            project = self._gitlab_project_target(target)
            res = self.gitlab.get_project_issue(project, int(issue_iid))
            return {"ok": res.get("ok"), "target": project, **res}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def create_gitlab_issue(self, target=None, title="", description="", labels=None):
        try:
            project = self._gitlab_project_target(target)
            res = self.gitlab.create_issue(project, title, description, labels)
            if res.get("ok"):
                self.activity.add("gitlab", "Created GitLab issue", title, metadata={"target": project})
            return {"ok": res.get("ok"), "target": project, **res}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def update_gitlab_issue(self, target=None, issue_iid=None, patch=None):
        try:
            project = self._gitlab_project_target(target)
            res = self.gitlab.update_issue(project, int(issue_iid), patch or {})
            if res.get("ok"):
                self.activity.add("gitlab", "Updated GitLab issue", f"#{issue_iid}", metadata={"target": project, "patch": patch or {}})
            return {"ok": res.get("ok"), "target": project, **res}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def create_gitlab_issue_note(self, target=None, issue_iid=None, body=""):
        try:
            project = self._gitlab_project_target(target)
            res = self.gitlab.create_issue_note(project, int(issue_iid), body)
            return {"ok": res.get("ok"), "target": project, **res}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def list_gitlab_epics(self, group=None, state="opened"):
        try:
            group = group or self.gitlab.default_group()
            if not group:
                raise ValueError("GitLab group path/id is required for epics")
            res = self.gitlab.list_group_epics(group, state=state or "opened")
            return {"ok": res.get("ok"), "group": group, **res}
        except Exception as e:
            return {"ok": False, "error": str(e), "epics": []}

    def create_gitlab_epic(self, group=None, title="", description="", labels=None):
        try:
            group = group or self.gitlab.default_group()
            if not group:
                raise ValueError("GitLab group path/id is required for epics")
            res = self.gitlab.create_group_epic(group, title, description, labels)
            return {"ok": res.get("ok"), "group": group, **res}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def update_gitlab_epic(self, group=None, epic_iid=None, patch=None):
        try:
            group = group or self.gitlab.default_group()
            if not group:
                raise ValueError("GitLab group path/id is required for epics")
            res = self.gitlab.update_group_epic(group, int(epic_iid), patch or {})
            return {"ok": res.get("ok"), "group": group, **res}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_troubleshooting_summary(self):
        return self.troubleshooting.summary()

    def list_app_logs(self):
        return self.troubleshooting.list_logs()

    def read_app_log(self, name, max_bytes=200000):
        return self.troubleshooting.read_log(name, max_bytes)

    def query_app_db(self, sql, limit=100):
        return self.troubleshooting.query_app_db(sql, limit)

    def list_workflows(self, project_id=None):
        try:
            project = self._project_for_api(project_id)
            return {"ok": True, "workflows": self.workflows.list_workflows(project["id"])}
        except Exception as e:
            return {"ok": False, "error": str(e), "workflows": []}

    def save_workflow(self, project_id=None, workflow=None):
        try:
            project = self._project_for_api(project_id)
            return {"ok": True, "workflow": self.workflows.save_workflow(project["id"], workflow or {})}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def run_workflow(self, workspace_id=None, workflow_id=None):
        try:
            workspace = self._workspace_for_api(workspace_id)
            return self.workflows.run_workflow(workflow_id, workspace)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def list_workflow_runs(self, workspace_id=None):
        try:
            workspace = self._workspace_for_api(workspace_id)
            return {"ok": True, "runs": self.workflows.list_workflow_runs(workspace["id"])}
        except Exception as e:
            return {"ok": False, "error": str(e), "runs": []}

    def list_session_automations(self, session_id=None):
        try:
            session_id = session_id or self.active_session_id
            return {"ok": True, "automations": self.automations.list_session_automations(session_id) if session_id else []}
        except Exception as e:
            return {"ok": False, "error": str(e), "automations": []}

    def save_session_automation(self, session_id=None, automation=None):
        try:
            session_id = session_id or self.active_session_id
            if not session_id:
                raise ValueError("No session selected")
            return {"ok": True, "automation": self.automations.save_session_automation(session_id, automation or {})}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def delete_session_automation(self, automation_id=None):
        try:
            return self.automations.delete_session_automation(automation_id)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_app_settings(self):
        settings = self.settings.get_settings()
        if settings.get("gitlab_token"):
            settings["gitlab_token"] = ""
            settings["gitlab_token_configured"] = "1"
        return {"ok": True, "settings": settings}

    def update_app_settings(self, patch=None):
        try:
            return {"ok": True, "settings": self.settings.update_settings(patch or {})}
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
                    res = self._start_blocking()          # reconnect now authenticated
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
        try:
            path = (self.backend.working_dir if self.backend else None) or _load_prefs().get("workdir") or os.path.expanduser("~")
            self.sessions.migrate_legacy_history(HISTORY_FILE, path)
        except Exception:
            pass
        return self.sessions.list_conversations()

    def get_conversation(self, conv_id: str):
        return self.sessions.get_conversation(conv_id)

    def save_conversation(self, conv_id: str, title: str, messages: list):
        cwd = (self.backend.working_dir if self.backend else None) or _load_prefs().get("workdir") or os.path.expanduser("~")
        return self.sessions.save_conversation(conv_id, title, messages, cwd)

    def delete_conversation(self, conv_id: str):
        return self.sessions.archive_session(conv_id)

    def clear_history(self):
        for conv in self.sessions.list_conversations():
            self.sessions.archive_session(conv["id"])
        return {"ok": True}


def main():
    # Optional: `python app.py --host https://your-co.ghe.com` for GitHub Enterprise.
    import sys
    if "--host" in sys.argv:
        i = sys.argv.index("--host")
        if i + 1 < len(sys.argv):
            os.environ["COPILOT_HOST"] = sys.argv[i + 1]
    if "--no-copilot" in sys.argv:
        os.environ["COPILOT_SKIP_START"] = "1"
    if "--webview-private" in sys.argv:
        os.environ["COPILOT_WEBVIEW_PRIVATE"] = "1"
    if "--webview-persist" in sys.argv:
        os.environ["COPILOT_WEBVIEW_PERSIST"] = "1"
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
    # On Windows, use a persistent WebView2 profile by default. The asset URLs are
    # cache-busted, and a persistent profile avoids private-profile startup hangs
    # seen on some managed desktops. COPILOT_WEBVIEW_PRIVATE=1 remains available if
    # a user's WebView2 profile is corrupt.
    wv_data = os.path.join(HISTORY_DIR, "webview2")
    use_private_webview = _env_flag("COPILOT_WEBVIEW_PRIVATE", False)
    persist_webview = _env_flag("COPILOT_WEBVIEW_PERSIST", os.name == "nt")
    if use_private_webview:
        persist_webview = False
    if not use_private_webview:
        try:
            os.makedirs(wv_data, exist_ok=True)
        except Exception:
            pass
    _dbg("main: webview.start() — gap to 'api.list_conversations' = WebView init + bridge injection")
    # gui=None lets pywebview pick the platform's webview (EdgeWebView2 on Win11).
    gui = os.environ.get("COPILOT_WEBVIEW_GUI") or None
    debug = _env_flag("COPILOT_WEBVIEW_DEBUG")
    http_server = _env_flag("COPILOT_WEBVIEW_HTTP", os.name == "nt")
    try:
        if use_private_webview:
            webview.start(gui=gui, debug=debug, http_server=http_server, private_mode=True)
        else:
            webview.start(gui=gui, debug=debug, http_server=http_server, private_mode=False, storage_path=wv_data)
    except TypeError:
        webview.start()   # older pywebview without these kwargs


if __name__ == "__main__":
    main()
