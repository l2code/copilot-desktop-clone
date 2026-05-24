@echo off
REM ============================================================================
REM  vendor-setup.bat  --  run ONCE (needs internet)
REM
REM  Installs the app's Python dependencies into a local "deps" folder next to
REM  this script (instead of into the Python install). Keep this whole folder on
REM  OneDrive: the packages are just files, so they survive a machine reimage.
REM  After this, run.bat launches the app WITHOUT reinstalling anything.
REM
REM  NOTE: deps are built for the Python version used here (pin to 3.12, which
REM  has prebuilt pythonnet wheels). If your weekly Python install changes minor
REM  version (e.g. 3.13), re-run this script once to rebuild deps.
REM ============================================================================
setlocal
set "HERE=%~dp0"
echo Installing dependencies into "%HERE%deps"  (one-time, needs internet)...
py -3.12 -m pip install --upgrade --target "%HERE%deps" -r "%HERE%requirements.txt"
if errorlevel 1 (
  echo.
  echo  py -3.12 not found or install failed; trying the default "python"...
  python -m pip install --upgrade --target "%HERE%deps" -r "%HERE%requirements.txt"
)
echo.
echo Done. From now on, just run run.bat -- no reinstall needed.
endlocal
pause
