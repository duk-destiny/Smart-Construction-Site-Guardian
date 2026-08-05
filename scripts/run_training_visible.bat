@echo off
cd /d "%~dp0.."
echo Training started. Close this window to abort.
".venv313\Scripts\python.exe" "scripts\train_combined.py" > "data\train\train_combined.visible.log" 2>&1
echo.
echo Training finished.
pause
