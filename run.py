#!/usr/bin/env python3
"""
DeKodX entry point.

GUI:   python run.py            (requires PyQt6 - see install.bat / requirements.txt)
CLI:   python run.py cli recover --source ~/.codex --output report.md
Audit: python run.py cli audit --logs --grep rate_limit
Capture: python run.py cli capture --interval 2.0
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _acquire_single_instance_lock() -> bool:
    """Try to acquire the DeKodX2 single-instance mutex.

    Returns True if this process owns the lock (first instance).
    Returns False if another instance already holds it.
    Uses a Windows named mutex via ctypes — reliable, no QCoreApplication needed.
    """
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    mutex_name = "Global\\DeKodX2-single-instance"

    # CreateMutexW: returns handle, sets ERROR_ALREADY_EXISTS if it exists
    handle = kernel32.CreateMutexW(None, False, mutex_name)
    if not handle:
        err = kernel32.GetLastError()
        if err == 183:  # ERROR_ALREADY_EXISTS
            return False
        # Some other error — treat as "already running" to be safe
        return False

    # Check if the mutex already existed (we got a handle but it was pre-existing)
    err = kernel32.GetLastError()
    if err == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        return False

    return True


def _bring_existing_to_front() -> None:
    """Bring the existing DeKodX window to the foreground (called when a second
    launch detects the mutex). Uses FindWindowW + SetForegroundWindow."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    # Find the DeKodX main window by its title
    title = "DeKodX - Codex Desktop Session Recovery v2"
    hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        return

    # Restore if minimized
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE

    # Set foreground window
    user32.SetForegroundWindow(hwnd)

    # Flash the taskbar to get attention
    user32.FlashWindow(hwnd, True)


def _release_single_instance_lock() -> None:
    """Release the single-instance mutex on exit."""
    import ctypes
    kernel32 = ctypes.windll.kernel32
    mutex_name = "Global\\DeKodX2-single-instance"
    handle = kernel32.OpenMutexW(0x00010000, False, mutex_name)  # SYNCHRONIZE
    if handle:
        kernel32.ReleaseMutex(handle)
        kernel32.CloseHandle(handle)


def _gui(argv: list[str]) -> int:
    try:
        from PyQt6.QtWidgets import QApplication
    except ImportError:
        print("DeKodX GUI needs PyQt6, which is not installed in this interpreter.\n"
              "  Windows : run install.bat once, then launch.bat\n"
              "  Anywhere: python -m pip install -r requirements.txt\n"
              "The headless CLI needs no dependencies:  python run.py cli --help")
        return 2

    # -- single instance guard ------------------------------------------------
    # A second copy of the app (double launch, stray shortcut) creates a second
    # always-on-top window that steals focus in a loop. Refuse to run twice:
    # focus the existing window instead of starting a new one.
    if not _acquire_single_instance_lock():
        print("DeKodX is already running - bringing the existing window to front.")
        _bring_existing_to_front()
        return 0

    from dex import APP_NAME, __version__
    from dex.ui import theme
    from dex.ui.main_window import MainWindow

    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName("DeKodX")
    theme.apply(app, theme.DEFAULT_ACCENT)

    initial = ""
    for flag in ("--source", "-s"):
        if flag in argv:
            initial = argv[argv.index(flag) + 1]
    window = MainWindow(initial_source=initial)
    window.show()
    rc = app.exec()
    _release_single_instance_lock()
    return rc


def main(argv: list[str]) -> int:
    if argv and argv[0] in ("cli", "--cli", "headless"):
        from dex.cli import main as cli_main
        return cli_main(argv[1:])
    return _gui(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
