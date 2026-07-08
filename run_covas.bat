@echo off
REM Double-click to launch COVAS++ (Phase 2 core loop).
cd /d "%~dp0"
".venv\Scripts\python.exe" "run_covas.py"
echo.
pause
