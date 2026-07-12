@echo off
REM Install devflow-daemon as a Windows Service using nssm.
REM
REM Prerequisites:
REM   - nssm installed and on PATH (https://nssm.cc/download)
REM   - Python venv with devflow-super installed (pip install -e ".[dev,web]")
REM   - config/workflow.yaml has daemon.enabled: true
REM
REM Usage:
REM   scripts\install-service.bat "C:\path\to\repo" "C:\path\to\venv\Scripts\python.exe"

setlocal

set REPO_PATH=%~1
set PYTHON_EXE=%~2

if "%REPO_PATH%"=="" (
    echo Usage: %~n0 "C:\path\to\repo" "C:\path\to\venv\Scripts\python.exe"
    exit /b 1
)
if "%PYTHON_EXE%"=="" (
    echo Usage: %~n0 "C:\path\to\repo" "C:\path\to\venv\Scripts\python.exe"
    exit /b 1
)

echo Installing devflow-daemon service...
echo   Repo:   %REPO_PATH%
echo   Python: %PYTHON_EXE%

nssm install devflow-daemon "%PYTHON_EXE%" "-m devflow.daemon --repo-path %REPO_PATH%"
nssm set devflow-daemon AppDirectory "%REPO_PATH%"
nssm set devflow-daemon AppStdout "%REPO_PATH%\logs\daemon.log"
nssm set devflow-daemon AppStderr "%REPO_PATH%\logs\daemon.log"
nssm set devflow-daemon AppRotateFiles 1
nssm set devflow-daemon AppRotateBytes 10485760
nssm set devflow-daemon Start SERVICE_AUTO_START

echo.
echo Service installed. Start with: nssm start devflow-daemon
echo View logs at: %REPO_PATH%\logs\daemon.log
echo Remove with: nssm remove devflow-daemon confirm

endlocal
