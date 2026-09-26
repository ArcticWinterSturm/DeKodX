@echo off
setlocal EnableExtensions
title DeKodX installer
cd /d "%~dp0"

echo ==========================================================
echo   DeKodX - one-time environment installer
echo   (creates .venv and installs PyQt6; needs internet once)
echo ==========================================================
echo.

where py >nul 2>nul
if %errorlevel%==0 (set "PY=py -3") else (set "PY=python")

%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python 3.9+ was not found on PATH.
    echo         Install it from https://www.python.org/downloads/
    echo         and tick "Add python.exe to PATH" during setup.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Creating virtual environment .venv ...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] venv creation failed.
        pause
        exit /b 1
    )
) else (
    echo [1/3] Virtual environment already present, reusing it.
)

echo [2/3] Upgrading pip ...
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet

echo [3/3] Installing dependencies (PyQt6 - one-time download) ...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Dependency installation failed - check your network connection.
    pause
    exit /b 1
)

echo.
echo ==========================================================
echo   Done. Double-click launch.bat to start DeKodX.
echo   Headless usage:  .venv\Scripts\python run.py cli --help
echo ==========================================================
pause
