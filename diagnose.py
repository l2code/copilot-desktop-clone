"""
diagnose.py  --  connectivity / proxy / auth doctor for the desktop app.

Run this in the same way you launch the app (same folder, same .env), but it
drives the Copilot SDK directly, step by step, with timeouts and verbose output —
so you can see exactly which step hangs or errors instead of staring at a blank
window.

    python diagnose.py

It prints your (redacted) proxy/env settings, then tries: start the CLI, check
sign-in, and list models — each bounded by a timeout. The first step that times
out or errors is where the app is getting stuck.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import time
import traceback

# Reuse the app's exact env/proxy setup so this mirrors a real launch.
try:
    from app import _load_env_file, _apply_copilot_proxy
except Exception as e:  # pragma: no cover
    print("Could not import app.py helpers:", e)
    def _load_env_file(): ...
    def _apply_copilot_proxy(): ...

from copilot import CopilotClient, SubprocessConfig


def _redact(url: str) -> str:
    return re.sub(r"://([^:/]+):[^@]+@", r"://\1:***@", url or "")


async def _step(name: str, coro, timeout: float):
    t = time.time()
    print(f"\n-> {name}  (timeout {timeout:.0f}s)", flush=True)
    try:
        res = await asyncio.wait_for(coro, timeout=timeout)
        print(f"   OK in {time.time() - t:.1f}s", flush=True)
        return res
    except asyncio.TimeoutError:
        print(f"   *** TIMED OUT after {timeout:.0f}s — the app hangs HERE ***", flush=True)
        raise
    except Exception as e:
        print(f"   *** ERROR: {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()
        raise


async def main() -> int:
    _load_env_file()
    _apply_copilot_proxy()

    print("=== Environment ===", flush=True)
    for k in ("COPILOT_ENV_FILE", "COPILOT_EXE", "COPILOT_HOST", "GITHUB_TOKEN",
              "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY",
              "NODE_USE_ENV_PROXY", "NODE_USE_SYSTEM_CA"):
        v = os.environ.get(k)
        if k in ("HTTPS_PROXY", "HTTP_PROXY") and v:
            v = _redact(v)
        if k == "GITHUB_TOKEN" and v:
            v = "<set>"
        print(f"  {k} = {v}", flush=True)

    cli_path = os.environ.get("COPILOT_EXE")
    kwargs = dict(
        github_token=os.environ.get("GITHUB_TOKEN") or None,
        use_logged_in_user=(not os.environ.get("GITHUB_TOKEN")),
        cwd=os.getcwd(),
        log_level="debug",
    )
    if cli_path and os.path.isfile(cli_path):
        kwargs["cli_path"] = cli_path
        print(f"\nUsing COPILOT_EXE: {cli_path}", flush=True)
    else:
        if cli_path:
            print(f"\nCOPILOT_EXE set but not found: {cli_path} — using bundled binary", flush=True)
        else:
            print("\nUsing the SDK's bundled copilot binary", flush=True)

    client = CopilotClient(SubprocessConfig(**kwargs), auto_start=False)
    try:
        await _step("client.start()  (spawn the copilot CLI)", client.start(), 30)
        status = await _step("get_auth_status()  (are you signed in?)", client.get_auth_status(), 30)
        print(f"   isAuthenticated = {getattr(status, 'isAuthenticated', None)}"
              f" | login = {getattr(status, 'login', None)}"
              f" | host = {getattr(status, 'host', None)}", flush=True)
        if getattr(status, "isAuthenticated", False):
            try:
                models = await _step("list_models()  (reach GitHub through the proxy)", client.list_models(), 30)
                ids = [getattr(m, "id", str(m)) for m in (models or [])]
                print(f"   models: {ids[:8]}", flush=True)
            except Exception:
                pass
            print("\n=== Auth + network look OK. If the app still hangs, the stall is in"
                  " session creation; tell me this output. ===", flush=True)
        else:
            print("\n=== Connected to the CLI but NOT signed in. Sign in via the Copilot"
                  " CLI, then re-run. ===", flush=True)
        return 0
    except Exception:
        print("\n=== Stopped at the failing step above. Share this output and I'll"
              " pinpoint the fix. ===", flush=True)
        return 1
    finally:
        try:
            await client.stop()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        pass
