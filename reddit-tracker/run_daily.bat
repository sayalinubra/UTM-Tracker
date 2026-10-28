@echo off
REM Daily Reddit tracker run, used by Windows Task Scheduler.
REM Uses the project's virtual environment (.venv) and appends output to data\daily.log
cd /d "%~dp0"
if not exist data mkdir data
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" run_daily.py >> "data\daily.log" 2>&1
