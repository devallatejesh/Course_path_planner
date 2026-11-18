@echo off
cd /d "%~dp0"
echo Starting CoursePath Planner...
echo Open http://localhost:8000 in your browser (press Ctrl+C here to stop)
start "" http://localhost:8000
py -3 server.py || python server.py
pause
