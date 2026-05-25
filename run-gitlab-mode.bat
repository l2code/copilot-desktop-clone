@echo off
REM Launch the app without starting GitHub Copilot automatically.
REM Use this when you mainly want the Workspace/GitLab/Troubleshoot panels or
REM when Copilot auth/proxy startup is slow on a managed Windows desktop.
setlocal
set "HERE=%~dp0"
set "COPILOT_SKIP_START=1"
set "COPILOT_WEBVIEW_PERSIST=1"
set "COPILOT_WEBVIEW_HTTP=1"

if exist "%HERE%venv\Scripts\python.exe" (
  "%HERE%venv\Scripts\python.exe" "%HERE%app.py" --no-copilot --webview-persist
) else (
  python "%HERE%app.py" --no-copilot --webview-persist
)
endlocal
