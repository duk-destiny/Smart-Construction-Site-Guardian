@echo off
cd /d "%~dp0"
".venv313\Scripts\python.exe" "scripts\mock_webhook_server.py" > "data\mock_webhook.out.log" 2> "data\mock_webhook.err.log"