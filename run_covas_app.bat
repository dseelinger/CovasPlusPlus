@echo off
REM Double-click to launch COVAS++ as a native window (the packaged experience).
cd /d "%~dp0"
".venv\Scripts\python.exe" "run_covas_app.py"
echo.
pause
