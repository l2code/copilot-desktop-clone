"""
terminal.py
===========
A lightweight integrated terminal for the desktop app. It runs each typed
command in the *currently active project folder* and streams output back to the
UI. On Windows it uses **PowerShell**; on macOS/Linux it falls back to bash.

This is a per-command runner (not a full PTY): `cd` is tracked so the working
directory persists between commands, output streams line-by-line, and a running
command can be interrupted. It does not host an interactive TTY, so curses-style
full-screen programs and per-session shell state (e.g. `$env:` vars) do not
persist between commands -- but everyday commands (git, ls/dir, python, npm)
work and run in the active folder.
"""

from __future__ import annotations

import os
import subprocess
import threading
from shutil import which


def _is_windows() -> bool:
    return os.name == "nt"


def powershell_exe() -> str:
    """Prefer PowerShell 7 (pwsh), then Windows PowerShell."""
    return which("pwsh") or which("powershell") or "powershell"


class Terminal:
    def __init__(self, cwd: str | None = None):
        self.cwd = cwd if (cwd and os.path.isdir(cwd)) else os.path.expanduser("~")
        self.proc: subprocess.Popen | None = None
        self._on_output = None   # callback(text)
        self._on_done = None     # callback(exit_code:int)
        self._lock = threading.Lock()

    def set_handlers(self, on_output, on_done):
        self._on_output = on_output
        self._on_done = on_done

    @property
    def shell_name(self) -> str:
        return "PowerShell" if _is_windows() else "bash"

    def set_cwd(self, path: str | None):
        if path and os.path.isdir(path):
            self.cwd = path

    def _argv(self, command: str):
        if _is_windows():
            return [powershell_exe(), "-NoProfile", "-NonInteractive", "-Command", command]
        return ["/bin/bash", "-lc", command]

    def _emit(self, text):
        if self._on_output and text:
            self._on_output(text)

    def _done(self, code):
        if self._on_done:
            self._on_done(int(code))

    def run(self, command: str):
        """Run one command in self.cwd, streaming output. Returns immediately;
        output/done arrive via the registered callbacks."""
        cmd = (command or "").strip()
        if not cmd:
            self._done(0)
            return

        # Intercept `cd` so the directory persists between commands (each command
        # otherwise runs in a fresh shell process).
        low = cmd.lower()
        if cmd == "cd" or low.startswith("cd ") or low.startswith("cd\t"):
            self._handle_cd(cmd[2:].strip())
            return

        def worker():
            rc = 0
            try:
                with self._lock:
                    self.proc = subprocess.Popen(
                        self._argv(cmd),
                        cwd=self.cwd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                    )
                proc = self.proc
                for line in proc.stdout:
                    self._emit(line)
                rc = proc.wait()
            except FileNotFoundError as e:
                self._emit(f"{e}\n")
                rc = 127
            except Exception as e:
                self._emit(f"{e}\n")
                rc = 1
            finally:
                with self._lock:
                    self.proc = None
                self._done(rc)

        threading.Thread(target=worker, daemon=True).start()

    def _handle_cd(self, target: str):
        target = target.strip().strip('"').strip("'")
        if not target or target == "~":
            newp = os.path.expanduser("~")
        elif os.path.isabs(target):
            newp = os.path.normpath(target)
        else:
            newp = os.path.normpath(os.path.join(self.cwd, target))
        if os.path.isdir(newp):
            self.cwd = newp
            self._emit(newp + "\n")
            self._done(0)
        else:
            self._emit(f"cd: no such directory: {target}\n")
            self._done(1)

    def interrupt(self):
        """Terminate the running command (best-effort)."""
        with self._lock:
            p = self.proc
        if p and p.poll() is None:
            try:
                p.terminate()
            except Exception:
                pass
