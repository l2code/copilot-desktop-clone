# Copilot Desktop

A small desktop chat app that talks to **GitHub Copilot** through GitHub's
official **Copilot SDK** (`github-copilot-sdk`). The UI is plain HTML/CSS/JS
rendered in a native window via **pywebview** (which uses the WebView2 runtime
already built into Windows 11 — Edge). Everything runs from interpreted Python,
so there is **no compiler and no admin install required**.

## Files

- `index.html` — the full UI (sidebar, model picker, streaming chat, code blocks).
  Open it directly in a browser to see a **demo** with simulated replies.
- `copilot_backend.py` — async wrapper around the Copilot SDK (auth, session, streaming).
- `app.py` — the pywebview desktop shell; bridges the JS UI to the backend.
- `storage.py` — SQLite schema and migrations for projects, workspaces, sessions,
  messages, activity, GitHub context, reviews, workflows, and attachments.
- `project_service.py`, `workspace_service.py`, `session_manager.py`,
  `activity.py`, `git_service.py`, `github_service.py`, `file_service.py`,
  `workflow_service.py`, `automation_service.py`, `settings_service.py` —
  backend services that move the app toward a Copilot App-style
  project/workspace/session model while preserving the current chat UI.
- `js/workbench.js` — the right-side workspace panel for Changes, Files,
  Activity, GitHub context, Workflows, and Terminal controls.
- `requirements.txt` — Python dependencies.

## Run it (non-admin, no compiler)

### 1. Get a portable Python (no admin)
Download **WinPython** (a self-contained zip) or the official **embeddable
Python** zip and unzip it anywhere you can write, e.g. `C:\Users\you\python`.
Nothing touches the registry or `Program Files`.

> If you use the *embeddable* zip, enable pip once:
> ```
> python.exe -m ensurepip
> ```
> (and uncomment the `import site` line in `python3xx._pth`).

### 2. Install dependencies
From this folder, using your portable Python:
```
python.exe -m pip install -r requirements.txt
```
On Windows, `requirements.txt` installs `pythonnet`, which lets pywebview drive
the Edge **WebView2** runtime. WebView2 ships with Windows 11. If it's somehow
missing, install the **Evergreen WebView2 Runtime** — the per-user installer
needs no admin rights. The Linux-only Qt packages in `requirements.txt` are
guarded with environment markers, so they are skipped on Windows.

On Linux, `requirements.txt` installs the Qt backend that pywebview needs
(`qtpy`, `PyQt6`, and `PyQt6-WebEngine`). If your distro blocks Qt WebEngine
because of missing system libraries, install the distro Qt/XCB packages and run
with:
```
PYWEBVIEW_GUI=qt python app.py
```

### 3. Authenticate with Copilot
The SDK uses your own Copilot identity (works with a **Copilot Business** seat).
Two options:

- **Use your logged-in account** (default): sign in once with the Copilot CLI /
  GitHub CLI device flow, then leave `app.py` as-is (`use_logged_in_user=True`).
- **Provide a token**: set an environment variable before launching:
  ```
  set GITHUB_TOKEN=ghu_xxx        (Command Prompt)
  $env:GITHUB_TOKEN="ghu_xxx"     (PowerShell)
  ```
  `app.py` passes this straight to the SDK.

See GitHub's "Authenticating with Copilot SDK" docs for the current device-flow
steps — the SDK manages the token exchange for you.

### 4. Launch
```
python.exe app.py
```
On Windows 11 you can also use:
```
run.bat
```
A native window opens. If auth succeeds you'll see a green
"Connected to GitHub Copilot" banner and replies stream live. If it fails, the
banner shows the error (usually "sign in first").

## How the pieces fit

```
 index.html  ──JS bridge──►  app.py (pywebview)
   (UI)                         │   asyncio loop on a background thread
                                ▼
                        copilot_backend.py
                                │   create_session(streaming=True)
                                ▼
                     github-copilot-sdk  ──►  GitHub Copilot
   ◄──── assistant.message_delta events stream back as you type ────
```

## Running on a machine that resets (e.g. a weekly reimage)

If your machine reinstalls Python every week but you don't want to reinstall the
Python packages each time, keep this folder somewhere persistent (e.g. OneDrive)
and vendor the dependencies once:

1. **One time, with internet:** double-click `vendor-setup.bat`. It installs the
   packages into a local `deps/` folder beside the script (not into Python).
2. **Every session after that:** double-click `run.bat`. It points the freshly
   installed Python at `deps/` via `PYTHONPATH` and launches the app — no `pip`.

Because `deps/` is just files, OneDrive persistence keeps it across reimages.
Notes:
- Pin Python to **3.12** (the version with prebuilt `pythonnet` wheels). If your
  weekly install changes minor version, re-run `vendor-setup.bat` once.
- Turn on **"Always keep on this device"** for the OneDrive folder so the files
  are present offline.
- `deps/` contains native files (`pythonnet`'s `.pyd`/`.dll`). If your employer's
  policy restricts storing binaries/executables on OneDrive the same way it does
  `.exe`, confirm that's allowed — otherwise keep a local `wheels/` folder of
  downloaded `.whl` files and install offline with
  `pip install --no-index --find-links wheels -r requirements.txt`.

## Behind a corporate proxy

The app does **not** read arbitrary project `.env` files. Network access (the
Copilot SDK subprocess) uses standard proxy environment variables —
`HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY` — inherited from the app's process.

- If you launch from a shell that already exports those (e.g. where the Copilot
  CLI worked), they're inherited automatically.
- For double-click launches, the app loads a `.env` next to `app.py` at startup.
  Create a `.env` (it's gitignored) with your proxy lines, or set `COPILOT_ENV_FILE`
  (see `run.bat`) to point at an existing `.env` without copying it. Existing
  environment variables are never overridden. Supported keys include
  `HTTP(S)_PROXY` / `NO_PROXY`, the Copilot CLI's `COPILOT_PROXY_USERNAME` /
  `COPILOT_PROXY_PASSWORD` / `COPILOT_PROXY_HOST`, `COPILOT_DESKTOP_CLI`, and
  `COPILOT_NO_DISCOVERY`.
- If you use the Copilot CLI's `COPILOT_PROXY_USERNAME` / `COPILOT_PROXY_PASSWORD`
  / `COPILOT_PROXY_HOST`, the app translates them into the standard proxy env it
  needs (URL-encoded `HTTP(S)_PROXY` plus `NODE_USE_ENV_PROXY` / `NODE_USE_SYSTEM_CA`,
  mirroring a `run-copilot.ps1` launcher) — the Node-based copilot binary needs
  those flags to use the proxy and trust the corporate CA.
- The app uses the SDK's **bundled** copilot binary (protocol-matched). It
  ignores the ambient `COPILOT_EXE`, because a newer system-installed `copilot.exe`
  can break the SDK handshake (`invalid literal for int()` on ping). Only set
  `COPILOT_DESKTOP_CLI` to override with a binary that matches `github-copilot-sdk`.

## Notes / next steps
- Tool permissions default to asking before file edits, shell commands, URL
  access, and MCP tool use. Reads are allowed, and Plan mode rejects actions
  that change state.
- Chat history is now stored in `~/.copilot-desktop/data.db`. On first run, the
  old `~/.copilot-desktop/history.json` file is imported into session/message
  tables and left in place with an extra timestamped backup.
- For isolated testing, set `COPILOT_DESKTOP_HOME` to point storage at a
  temporary app-data folder.
- The workspace workbench uses Git from `PATH`. GitHub panels use `GITHUB_TOKEN`,
  `GH_TOKEN`, or `gh auth token` and degrade gracefully when unauthenticated.
- To ship as a single `.exe`, run PyInstaller against `app.py` — still no admin,
  still no compiler toolchain needed.
- Model list is pulled live from `client.list_models()`; the picker in the top
  bar cycles through whatever your account has access to.
