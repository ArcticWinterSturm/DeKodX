@echo off
rem DeKodX launcher - debug mode with a live console for tracebacks
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call install.bat
if not exist ".venv\Scripts\python.exe" exit /b 1
".venv\Scripts\python.exe" run.py %*
pause
