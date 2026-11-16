@echo off
cd /d %~dp0
start "" http://127.0.0.1:8765
.venv\Scripts\python -m uvicorn clipper.app:app --host 127.0.0.1 --port 8765
