@echo off
REM Launch the full app with GitHub Copilot enabled.
REM
REM This uses a fresh WebView2 profile for each launch to avoid the "Not
REM Responding" bridge hang that can happen when stale app/WebView2 processes
REM keep the default profile locked on managed Windows desktops.
setlocal
set "HERE=%~dp0"
set "COPILOT_WEBVIEW_PERSIST=1"
set "COPILOT_WEBVIEW_HTTP=1"
set "COPILOT_WEBVIEW_STORAGE=%TEMP%\copilot-desktop-webview-%RANDOM%-%RANDOM%"
set "COPILOT_DEBUG=1"
set "COPILOT_TRANSPORT=tcp"
set "COPILOT_SKIP_START="

if exist "%HERE%venv\Scripts\python.exe" (
  "%HERE%venv\Scripts\python.exe" "%HERE%app.py" --webview-persist
) else (
  python "%HERE%app.py" --webview-persist
)
endlocal
