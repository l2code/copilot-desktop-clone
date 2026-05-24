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
On Windows this also pulls in `pythonnet`, which lets pywebview drive the
Edge **WebView2** runtime. WebView2 ships with Windows 11. If it's somehow
missing, install the **Evergreen WebView2 Runtime** — the per-user installer
needs no admin rights.

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

## Notes / next steps
- Tool permissions default to asking before file edits, shell commands, URL
  access, and MCP tool use. Reads are allowed, and Plan mode rejects actions
  that change state.
- To ship as a single `.exe`, run PyInstaller against `app.py` — still no admin,
  still no compiler toolchain needed.
- Model list is pulled live from `client.list_models()`; the picker in the top
  bar cycles through whatever your account has access to.
