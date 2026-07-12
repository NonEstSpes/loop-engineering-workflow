@echo off
REM Run devflow-daemon in foreground for debugging.
REM
REM Usage:
REM   scripts\run-daemon-dev.bat [repo_path]
REM
REM Make sure config/workflow.yaml has daemon.enabled: true before running.

setlocal

set REPO_PATH=%~1
if "%REPO_PATH%"=="" set REPO_PATH=.

echo Starting devflow-daemon in foreground (debug mode)...
echo   Repo: %REPO_PATH%
echo   Press Ctrl+C to stop.
echo.

python -m devflow.daemon --config-dir config --repo-path "%REPO_PATH%"

endlocal
