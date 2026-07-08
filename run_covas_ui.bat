@echo off
REM Double-click to launch COVAS++ with the web control panel (Phase 3).
cd /d "%~dp0"
".venv\Scripts\python.exe" "run_covas_ui.py"
echo.
pause
