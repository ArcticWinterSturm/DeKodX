"""Launch-relative output locations.

Every report the app writes lands inside the folder it was launched from
(C:\\Users\\User\\Documents\\DeKodX2 by default):
  Archive/   - recovery + chatwork reports (past work)
  Streamed/  - live-trap JSONL streams + session reports (live sessions)

Always relative to the DeKodX2 package root, never the CWD, so launching
from a shortcut or another shell still writes to the right place.
"""

from __future__ import annotations

from pathlib import Path

# .../DeKodX2/dex/ui/_paths.py -> package root is two parents up
APP_ROOT = Path(__file__).resolve().parent.parent.parent


def archive_dir() -> Path:
    d = APP_ROOT / "Archive"
    d.mkdir(parents=True, exist_ok=True)
    return d


def streamed_dir() -> Path:
    d = APP_ROOT / "Streamed"
    d.mkdir(parents=True, exist_ok=True)
    return d


def archive_report(name: str) -> Path:
    """Full path for a recovery/chatwork .md report inside Archive/."""
    return archive_dir() / name


def streamed_report(name: str) -> Path:
    """Full path for a live-trap session .md inside Streamed/."""
    return streamed_dir() / name
