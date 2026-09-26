"""Live analytics capture daemon for Codex Desktop.

Monitors ~/.codex for changes and captures the full information surface
that the desktop app-server writes — including events that are intentionally
not serialized to the rollout JSONL.

This is the highest-leverage piece for making DeKodX2 desktop-capable:
it captures the TrackEventsRequest upload stream locally before it leaves.

Env vars that enable this on the desktop:
  CODEX_ANALYTICS_EVENTS_CAPTURE_FILE = path to JSONL capture file (0600, append)
  CODEX_ROLLOUT_TRACE_ROOT = directory for per-request trace bundles

Reference: chatgpt-desktop-codex-research.md §4, §4b, §9
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Callable, Optional

from .analytics import TrackEventsRequest


class CaptureDaemon:
    """Monitors ~/.codex for analytics capture files and new rollout data."""

    def __init__(self, home: Path, *,
                 on_event: Optional[Callable[[TrackEventsRequest], None]] = None,
                 on_rollout_change: Optional[Callable[[Path], None]] = None,
                 poll_interval: float = 2.0):
        self.home = Path(home)
        self.on_event = on_event
        self.on_rollout_change = on_rollout_change
        self.poll_interval = poll_interval
        self._running = False
        self._rollout_mtimes: dict[str, float] = {}
        self._analytics_offset: dict[str, int] = {}

    def start(self, blocking: bool = True) -> None:
        """Start the capture daemon."""
        self._running = True
        if blocking:
            self._run_loop()
        # Non-blocking mode would use a thread — left to the caller

    def stop(self) -> None:
        """Stop the capture daemon."""
        self._running = False

    def _run_loop(self) -> None:
        """Main polling loop."""
        print(f"[{time.strftime('%H:%M:%S')}] Capture daemon started: {self.home}")
        print(f"  Polling every {self.poll_interval}s")
        print("  Press Ctrl+C to stop\n")

        # Initial scan
        self._scan_rollouts()

        try:
            while self._running:
                self._scan_rollouts()
                self._scan_analytics_files()
                time.sleep(self.poll_interval)
        except KeyboardInterrupt:
            print(f"\n[{time.strftime('%H:%M:%S')}] Capture daemon stopped")

    def _scan_rollouts(self) -> None:
        """Check for new or modified rollout files."""
        if self.on_rollout_change is None:
            return

        for pattern in ("sessions/**/*.jsonl", "archived_sessions/*.jsonl", "trim_archives/**/*.jsonl"):
            for p in sorted(self.home.glob(pattern)):
                try:
                    mtime = p.stat().st_mtime
                except OSError:
                    continue
                key = str(p)
                if key not in self._rollout_mtimes or self._rollout_mtimes[key] != mtime:
                    self._rollout_mtimes[key] = mtime
                    try:
                        self.on_rollout_change(p)
                    except Exception as exc:
                        print(f"  error in rollout handler: {exc}")

    def _scan_analytics_files(self) -> None:
        """Check for new analytics capture files."""
        if self.on_event is None:
            return

        # Standard location for capture file
        capture_paths = [
            self.home / "audit" / "events.jsonl",
            Path.home() / ".codex" / "audit" / "events.jsonl",
        ]

        # Also check env var
        env_path = os.environ.get("CODEX_ANALYTICS_EVENTS_CAPTURE_FILE")
        if env_path:
            capture_paths.insert(0, Path(env_path))

        for capture_path in capture_paths:
            if not capture_path.exists():
                continue
            self._read_analytics_file(capture_path)

    def _read_analytics_file(self, path: Path) -> None:
        """Read new lines from an analytics capture file."""
        key = str(path)
        last_offset = self._analytics_offset.get(key, 0)

        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(last_offset)
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        event = TrackEventsRequest.from_dict(data)
                        if event.facts:
                            self.on_event(event)
                    except json.JSONDecodeError:
                        pass
                self._analytics_offset[key] = f.tell()
        except OSError:
            pass


def run_capture_daemon(home: str | Path, *, interval: float = 2.0) -> None:
    """Run a simple capture daemon that prints events as they arrive."""
    home = Path(home)
    daemon = CaptureDaemon(home, poll_interval=interval)

    def on_event(event: TrackEventsRequest) -> None:
        fact_types = [type(f).__name__ for f in event.facts]
        print(f"[{event.timestamp}] {event.event_type}: {', '.join(fact_types)}")

    def on_rollout_change(path: Path) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] Rollout changed: {path.name}")

    daemon.on_event = on_event
    daemon.on_rollout_change = on_rollout_change
    daemon.start(blocking=True)


if __name__ == "__main__":
    import sys
    home = sys.argv[1] if len(sys.argv) > 1 else str(Path.home() / ".codex")
    interval = float(sys.argv[2]) if len(sys.argv) > 2 else 2.0
    run_capture_daemon(home, interval=interval)
