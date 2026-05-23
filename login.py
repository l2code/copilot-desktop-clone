"""
login.py — one-time Copilot authentication.

Runs the Copilot CLI that ships inside github-copilot-sdk (no separate install)
through its OAuth device flow. After this succeeds once, app.py can connect with
use_logged_in_user=True. Re-run only if your login expires.

Usage:  python login.py
"""

import os
import subprocess
import sys

import copilot


def cli_path() -> str:
    base = os.path.join(os.path.dirname(copilot.__file__), "bin")
    for name in ("copilot.exe", "copilot"):  # .exe on Windows, no ext elsewhere
        p = os.path.join(base, name)
        if os.path.exists(p):
            return p
    raise FileNotFoundError("Bundled Copilot CLI not found in the SDK package.")


if __name__ == "__main__":
    sys.exit(subprocess.run([cli_path(), "login", *sys.argv[1:]]).returncode)
