@echo off
cd /d "%~dp0.."
".venv313\Scripts\python.exe" "scripts\train_combined.py" > "data\train\train_combined.out.log" 2>&1
