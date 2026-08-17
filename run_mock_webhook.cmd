@echo off
cd /d "%~dp0"
REM 自动检测虚拟环境，都没有则用系统 python（mock webhook 仅依赖 stdlib，无需装包）
if exist ".venv313\Scripts\python.exe" (
    set "PY=.venv313\Scripts\python.exe"
) else if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else if exist "venv\Scripts\python.exe" (
    set "PY=venv\Scripts\python.exe"
) else (
    set "PY=python"
)
"%PY%" "scripts\mock_webhook_server.py" > "data\mock_webhook.out.log" 2> "data\mock_webhook.err.log"
