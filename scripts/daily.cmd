@echo off
rem Windows Task Scheduler runs this once a day (python -m interniq daily).
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" -m interniq daily >> "data\daily.log" 2>&1
