@echo off
rem DeKodX launcher - windowed mode (no console)
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" call install.bat
if not exist ".venv\Scripts\pythonw.exe" exit /b 1
start "" ".venv\Scripts\pythonw.exe" run.py %*
