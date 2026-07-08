@echo off
REM Double-click this to verify your COVAS++ setup.
cd /d "%~dp0"
".venv\Scripts\python.exe" "check_setup.py"
echo.
pause
