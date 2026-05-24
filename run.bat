@echo off
REM ============================================================================
REM  run.bat  --  launch the app each session (no install needed)
REM
REM  Points a freshly-installed Python at the pre-installed "deps" folder via
REM  PYTHONPATH, then starts the app. Safe to run after a machine reimage as long
REM  as: (1) Python is installed (your weekly Chocolatey step), and (2) the deps
REM  folder exists (created once by vendor-setup.bat) and is synced locally.
REM ============================================================================
setlocal
set "HERE=%~dp0"
set "PYTHONPATH=%HERE%deps"

REM Proxy / env: the app auto-loads a ".env" next to this script. To instead use
REM your existing devpod .env, uncomment and point COPILOT_ENV_FILE at it:
REM set "COPILOT_ENV_FILE=%USERPROFILE%\OneDrive - UBS\projects\devpod\.env"
if not exist "%HERE%deps" (
  echo Dependencies not found. Run vendor-setup.bat once first ^(needs internet^).
  pause
  exit /b 1
)
py -3.12 "%HERE%app.py"
if errorlevel 1 python "%HERE%app.py"
endlocal
