"""Live Trap tab: the complete trap telemetry engine embedded in DeKodX.

Two modes:
  - AUDIT (red dot): passive read-only audit of .codex — inventory, schema
    checks, and a dry-run of every poller WITHOUT writing JSONL streams.
  - LIVE (green dot): full interception — all pollers armed, writing the six
    JSONL streams (tokens/ttft/thinking/netlog/usage/bytes) to the output
    directory. On stop, a session Markdown report is written.

Everything is READ-ONLY on ~/.codex. Streams land in the chosen output dir.

Threading model (critical on Windows):
  Every poller runs on a dedicated QThread (``TrapWorker``), NEVER on the Qt
  GUI thread. The GUI thread only renders label updates and log lines that
  arrive as queued signals. This guarantees the window stays responsive no
  matter what the pollers are doing — sqlite busy-waits, disk I/O, bursts of
  hundreds of rows when new sessions start.

  The byte counter no longer spawns ``netstat``: on Windows it reads the
  interface table straight from the network stack via
  ``iphlpapi.GetIfTable`` (a plain syscall, microseconds, no process spawn,
  no AV interference). Spawning a process ~10x per second from a 100 ms
  timer was locking up Windows machines — no more.

  Pollers arm their cursors at the current tail on start (thinking now arms
  ``updated_at_ordinal`` too, so weeks-old reasoning items are never replayed
  as "live"), and baseline state (threads, spinoff threads) is primed
  silently on the first poll so a restart does not re-emit every row.
"""

from __future__ import annotations

import ctypes
import json
import os
import sqlite3
import sys
import time
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ._paths import streamed_dir
from .widgets import ConsoleLog, PathRow, SectionChip, rich_tip

CODEX_DIR = Path.home() / ".codex"

NETLOG_TARGETS = ("hyper%", "reqwest%", "codex_core%", "codex_http_client%")

# Poll cadence (ms). All of it runs off the GUI thread, so a slow poll can
# only delay itself, never freeze the app. ``bytes`` is 1 s: the stream is
# meant for humans, and it was the heaviest poller (one netstat spawn each).
# ``attach`` re-scans the sessions tree so rollouts created while the engine
# is running (new sessions launched in the app) get tailed too.
POLL_MS = {
    "rollout": 80,
    "netlog": 100,
    "thinking": 150,
    "usage": 1000,
    "bytes": 1000,
    "attach": 2000,
}

# A live .codex is written to constantly by the desktop app. "Wait up to 0.3 s
# for the lock, else skip this tick" is the right semantics for a live
# tailer — the cursor catches up on the next tick. The old 2 s wait, repeated
# 10x per second on the GUI thread, was freezing the UI solid.
SQLITE_BUSY_TIMEOUT = 0.3

ERR_LOG_INTERVAL = 10.0  # seconds between repeats of the same error


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def wall_iso(ms: int | None = None) -> str:
    dt = datetime.fromtimestamp((ms or now_ms()) / 1000)
    return dt.strftime("%Y-%m-%d %H:%M:%S.") + f"{dt.microsecond // 1000:03d}"


def _connect_ro(path: Path):
    try:
        return sqlite3.connect(
            f"file:{Path(path).as_posix()}?mode=ro", uri=True,
            timeout=SQLITE_BUSY_TIMEOUT)
    except sqlite3.Error:
        return None


def _to_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ bytes ----
# Interface byte counters without spawning a single process.

class _MIB_IFROW(ctypes.Structure):
    """iphlpapi MIB_IFROW (counters are ULongLong — a classic gotcha)."""

    _fields_ = [
        ("dwIndex", wintypes.DWORD),
        ("dwType", wintypes.DWORD),
        ("dwMtu", wintypes.DWORD),
        ("dwSpeed", wintypes.DWORD),
        ("dwPhysAddrLen", wintypes.DWORD),
        ("dwDescrLen", wintypes.DWORD),
        ("dwIfStatus", wintypes.DWORD),
        ("dwIfType", wintypes.DWORD),
        ("dwLastChange", wintypes.DWORD),
        ("dwInOctets", ctypes.c_uint64),
        ("dwOutOctets", ctypes.c_uint64),
        ("dwInUcastPkts", ctypes.c_uint64),
        ("dwOutUcastPkts", ctypes.c_uint64),
        ("dwInNonUcastPkts", ctypes.c_uint64),
        ("dwOutNonUcastPkts", ctypes.c_uint64),
        ("dwInDiscards", ctypes.c_uint64),
        ("dwOutDiscards", ctypes.c_uint64),
        ("dwInErrors", ctypes.c_uint64),
        ("dwOutErrors", ctypes.c_uint64),
        ("dwInUnknownProtos", ctypes.c_uint64),
        ("dwOutQLen", ctypes.c_uint64),
        ("bDescr", wintypes.BYTE * 256),
        ("bPhysAddr", wintypes.BYTE * 8),
    ]


_IF_TYPE_SOFTWARE_LOOPBACK = 24
_ERROR_INSUFFICIENT_BUFFER = 122  # ERROR_INSUFFICIENT_BUFFER (0x7A)


def _win_iface_bytes() -> tuple[int, int] | None:
    """(rx, tx) physical-interface byte totals via iphlpapi.GetIfTable.

    A direct read from the network stack: no process spawn, no stdout to
    parse, nothing for real-time AV to scan. Sums all non-loopback
    interfaces (the old netstat parser could only ever see the first row).
    """
    iphlp = ctypes.WinDLL("iphlpapi", use_last_error=True)
    size = wintypes.DWORD(0)
    if iphlp.GetIfTable(None, ctypes.byref(size), False) != _ERROR_INSUFFICIENT_BUFFER:
        return None
    if not size.value:
        return None
    buf = ctypes.create_string_buffer(size.value)
    if iphlp.GetIfTable(buf, ctypes.byref(size), False) != 0:
        return None
    base = ctypes.addressof(buf)
    num_entries = wintypes.DWORD.from_address(base).value
    row_size = ctypes.sizeof(_MIB_IFROW)
    rx = tx = 0
    for i in range(num_entries):
        row = _MIB_IFROW.from_address(
            base + ctypes.sizeof(wintypes.DWORD) + i * row_size)
        if row.dwIfType == _IF_TYPE_SOFTWARE_LOOPBACK:
            continue
        rx += row.dwInOctets
        tx += row.dwOutOctets
    return rx, tx


def _proc_iface_bytes() -> tuple[int, int] | None:
    """Linux: sum /proc/net/dev (rx, tx) over all non-loopback interfaces."""
    rx = tx = 0
    found = False
    with open("/proc/net/dev", "r", encoding="ascii") as fh:
        for line in fh:
            if ":" not in line:
                continue
            name, rest = line.split(":", 1)
            if name.strip() == "lo":
                continue
            fields = rest.split()
            if len(fields) >= 2:
                rx += int(fields[0])
                tx += int(fields[1])
                found = True
    return (rx, tx) if found else None


def _netstat_iface_bytes() -> tuple[int, int] | None:
    """Last-resort fallback: parse ``netstat -e`` data rows.

    A data row ends with six integers (InOctets OutOctets InUcastPkts
    OutUcastPkts InNonUcastPkts OutNonUcastPkts); header lines never do, so
    no line-position assumptions are needed. Sums every non-loopback
    interface.
    """
    import subprocess
    out = subprocess.run(["netstat", "-e"], capture_output=True,
                         text=True, timeout=5).stdout
    rx = tx = 0
    found = False
    for line in out.splitlines():
        if "loopback" in line.lower():
            continue
        parts = line.split()
        if len(parts) < 8:
            continue
        try:
            vals = [int(x) for x in parts[-6:]]
        except ValueError:
            continue  # header / junk line
        rx += vals[0]
        tx += vals[1]
        found = True
    return (rx, tx) if found else None


def iface_bytes() -> tuple[int, int] | None:
    """Total (rx, tx) interface bytes, cheapest reliable source first."""
    if sys.platform == "win32":
        try:
            result = _win_iface_bytes()
            if result is not None:
                return result
        except Exception:
            pass
    if os.path.exists("/proc/net/dev"):
        try:
            return _proc_iface_bytes()
        except (OSError, ValueError):
            pass
    try:
        return _netstat_iface_bytes()
    except Exception:
        return None


# ------------------------------------------------------------------ engine ---

class TrapEngine:
    """All five pollers. Thread-agnostic: drive ``tick()`` from any thread
    (the app uses a dedicated QThread; tests call it directly)."""

    def __init__(self, out_dir: Path, live: bool, log, codex_root: Path | None = None):
        self.out_dir = Path(out_dir)
        self.live = live
        self.log = log
        root = Path(codex_root) if codex_root else CODEX_DIR
        self.sessions_dir = root / "sessions"
        self.logs_db = root / "logs_2.sqlite"
        self.state_db = root / "state_5.sqlite"
        self.catalog_db = root / "sqlite" / "codex-dev.db"
        self.thread_history_db = root / "thread_history_1.sqlite"
        self.started_ms = now_ms()
        self.counts = {"tokens": 0, "ttft": 0, "thinking": 0,
                       "netlog": 0, "usage": 0, "bytes": 0}
        self._fh = {}
        # cursors
        self.rollout_tails: dict[str, dict] = {}
        self.rollouts_scanned_once = False
        self.turns: dict[str, dict] = {}          # path -> current turn state
        self.netlog_last_id = 0
        self.netlog_cols: set | None = None       # cached PRAGMA table_info(logs)
        self.thinking_armed = False
        self.thinking_last_rowid = 0
        self.thinking_last_ord = 0
        self.thinking_seen: dict[str, tuple] = {}
        self.prev_threads: dict = {}
        self.prev_spins: dict = {}
        self.prev_seq: dict = {}
        self.state_cols: set | None = None        # cached PRAGMA table_info(threads)
        self.prev_bytes = None
        self._last_err: dict[tuple, float] = {}
        self._next_due = {k: 0.0 for k in POLL_MS}
        self.ttft_events: list[dict] = []         # for the session MD report
        self.token_events: list[dict] = []
        self.think_events: list[dict] = []
        self.spin_events: list[dict] = []
        self._last_ttft_ms: int | None = None
        self._last_ttft_model: str | None = None

    # -- logging helpers -----------------------------------------------------
    def _err_log(self, stream: str, msg: str) -> None:
        """Log a poller error at most once per ERR_LOG_INTERVAL seconds.

        A missing table or a held lock used to be re-logged 10x per second,
        flooding the console (each append re-renders the QPlainTextEdit).
        """
        now = time.monotonic()
        key = (stream, msg[:160])
        last = self._last_err.get(key)
        if last is None or now - last >= ERR_LOG_INTERVAL:
            self._last_err[key] = now
            self.log(f"{stream}: {msg}", "warn")

    # -- stream writing ------------------------------------------------------
    def _w(self, stream: str, rec: dict) -> None:
        self.counts[stream] += 1
        if not self.live:
            return
        day = datetime.now().strftime("%Y%m%d")
        fh = self._fh.get(stream)
        if fh is None or getattr(fh, "_day", None) != day:
            if fh:
                try:
                    fh.close()
                except Exception:
                    pass
            self.out_dir.mkdir(parents=True, exist_ok=True)
            fh = open(self.out_dir / f"{stream}-{day}.jsonl", "a", encoding="utf-8")
            fh._day = day
            self._fh[stream] = fh
        # No per-record flush: a burst of hundreds of rows per tick used to
        # mean hundreds of synchronous flushes. One flush per tick instead.
        fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

    def _flush(self) -> None:
        for fh in self._fh.values():
            try:
                fh.flush()
            except Exception:
                pass

    # -- rollout tailing -------------------------------------------------------
    def scan_rollouts(self) -> int:
        """(Re)scan the sessions tree, attach any rollout file not yet tailed.

        Called once at start and periodically (POLL_MS["attach"]) so sessions
        launched while the engine is running get picked up. Returns how many
        new files were attached.
        """
        root = self.sessions_dir
        if not root.is_dir():
            return 0
        try:
            files = root.rglob("rollout-*.jsonl")
        except OSError:
            return 0
        first_scan = not self.rollouts_scanned_once
        self.rollouts_scanned_once = True
        added = 0
        for f in files:
            p = str(f)
            if p in self.rollout_tails:
                continue
            try:
                if not f.is_file():
                    continue
                size = f.stat().st_size
            except OSError:
                continue
            # First scan: everything enumerated here already exists (even
            # actively-written sessions) — tail from EOF. Later rescans only
            # ever see files created after the engine started — brand-new
            # sessions, read from the beginning. No mtime/clock comparison
            # involved, so filesystem clock jitter can't misclassify files.
            offset = size if first_scan else 0
            try:
                fh = open(f, "r", encoding="utf-8", errors="replace")
            except OSError:
                continue
            fh.seek(offset)
            self.rollout_tails[p] = {"fh": fh, "offset": offset}
            added += 1
            self.log(f"attached live rollout: {f.name}",
                     "ok" if offset == 0 else "info")
        return added

    def poll_rollouts(self) -> None:
        for p, t in list(self.rollout_tails.items()):
            fh = t["fh"]
            # Rotated/truncated/removed mid-tail? Resync (or drop; the
            # periodic rescan re-attaches if the file reappears).
            try:
                if os.path.getsize(p) < t["offset"]:
                    fh.close()
                    self.rollout_tails.pop(p, None)
                    self.log(f"rollout rotated/truncated, re-attaching: "
                             f"{Path(p).name}", "warn")
                    continue
            except OSError:
                try:
                    fh.close()
                except Exception:
                    pass
                self.rollout_tails.pop(p, None)
                self.log(f"rollout file removed: {Path(p).name}", "warn")
                continue
            n = 0
            try:
                while True:
                    raw = fh.readline()
                    if not raw:
                        break
                    n += 1
                    self._handle_line(p, raw)
                if n:
                    t["offset"] = fh.tell()
            except Exception:
                try:
                    fh.close()
                except Exception:
                    pass
                self.rollout_tails.pop(p, None)
                self.log(f"rollout tail lost, will re-attach: {Path(p).name}",
                         "warn")

    def _handle_line(self, path: str, raw: str) -> None:
        try:
            rec = json.loads(raw)
        except Exception:
            return
        typ = rec.get("type")
        p = rec.get("payload") or {}
        if not isinstance(p, dict):
            return
        ta = self.turns.get(path)
        if typ == "turn_context":
            # merge, never clobber a running clock (task_started arrives first)
            prev = ta if (ta and ta.get("turn_id") == p.get("turn_id")) else {
                "turn_id": p.get("turn_id"), "start_ms": None,
                "first_ms": None, "reconciled": False}
            prev["model"] = p.get("model")
            prev["effort"] = p.get("effort")
            self.turns[path] = prev
        elif typ == "event_msg":
            et = p.get("type")
            if et == "task_started":
                # desktop rollouts never emit user_message; task_started is the marker
                if ta is None or ta.get("turn_id") != p.get("turn_id"):
                    ta = self.turns[path] = {"turn_id": p.get("turn_id"),
                                             "start_ms": None, "first_ms": None,
                                             "reconciled": False, "model": None,
                                             "effort": None}
                ta["start_ms"] = now_ms()
                ta["started_at"] = p.get("started_at")
                self._w("ttft", {"ts_ms": ta["start_ms"], "wall": wall_iso(ta["start_ms"]),
                                 "turn_id": ta.get("turn_id"), "event": "turn_start"})
            elif et == "token_count":
                info = p.get("info") or {}
                tlu = info.get("last_token_usage") or {}
                ttu = info.get("total_token_usage") or {}
                # rate_limits rides at payload level in this build
                rl = p.get("rate_limits") or info.get("rate_limits") or {}
                prim = rl.get("primary") or {}
                row = {"ts_ms": now_ms(), "wall": wall_iso(),
                       "turn_id": p.get("turn_id"),
                       "input_tokens": tlu.get("input_tokens"),
                       "cached_input_tokens": tlu.get("cached_input_tokens"),
                       "output_tokens": tlu.get("output_tokens"),
                       "total_tokens": tlu.get("total_tokens"),
                       "cum_total_tokens": ttu.get("total_tokens"),
                       "context_window": info.get("model_context_window"),
                       "plan_type": rl.get("plan_type"),
                       "used_percent": prim.get("used_percent"),
                       "window_minutes": prim.get("window_minutes"),
                       "resets_at": prim.get("resets_at"),
                       "secondary_used_percent": (rl.get("secondary") or {}).get("used_percent")}
                self._w("tokens", row)
                if len(self.token_events) < 5000:
                    self.token_events.append(row)
            elif et == "task_complete" and ta and not ta.get("reconciled"):
                live = ta.get("start_ms") and ta.get("first_ms")
                row = {"ts_ms": now_ms(), "wall": wall_iso(),
                       "turn_id": ta.get("turn_id"), "event": "task_complete",
                       "app_ttft_ms": p.get("time_to_first_token_ms"),
                       "duration_ms": p.get("duration_ms"),
                       "live_ttft_ms": (ta["first_ms"] - ta["start_ms"]) if live else None,
                       "model": ta.get("model"), "effort": ta.get("effort")}
                self._w("ttft", row)
                self.ttft_events.append(row)
                ta["reconciled"] = True
        elif typ in ("response_item", "token_usage_record"):
            role = p.get("role")
            if ta and ta.get("start_ms") and not ta.get("first_ms") and (
                    p.get("type") == "reasoning" or
                    (p.get("type") == "message" and role == "assistant")):
                ta["first_ms"] = now_ms()
                row = {"ts_ms": ta["first_ms"], "wall": wall_iso(ta["first_ms"]),
                       "turn_id": ta.get("turn_id"), "event": "first_response_item",
                       "item_type": p.get("type"),
                       "live_ttft_ms": ta["first_ms"] - ta["start_ms"],
                       "model": ta.get("model"), "effort": ta.get("effort")}
                self._w("ttft", row)
                self.ttft_events.append(row)
                self._last_ttft_ms = ta["first_ms"] - ta["start_ms"]
                self._last_ttft_model = ta.get("model")

    # -- netlog -----------------------------------------------------------------
    def poll_netlog(self) -> None:
        c = _connect_ro(self.logs_db)
        if c is None:
            return
        try:
            if not self.netlog_last_id:
                self.netlog_last_id = int(
                    c.execute("SELECT COALESCE(MAX(id),0) FROM logs").fetchone()[0])
                self.log(f"netlog cursor armed at id={self.netlog_last_id} "
                         f"(rows pruned below this are already gone)", "info")
                return
            if self.netlog_cols is None:
                cols = {r[1] for r in c.execute("PRAGMA table_info(logs)")}
                need = ("id", "ts", "level", "target")
                if any(col not in cols for col in need):
                    self._err_log("netlog", f"logs table missing {need}; poller disabled")
                    self.netlog_cols = set()
                    return
                self.netlog_cols = cols
            has_nanos = "ts_nanos" in self.netlog_cols
            body = "feedback_log_body" if "feedback_log_body" in self.netlog_cols \
                else "NULL AS feedback_log_body"
            conds = " OR ".join(["target LIKE ?"] * len(NETLOG_TARGETS))
            rows = c.execute(
                f"SELECT id, ts{', ts_nanos' if has_nanos else ', 0 AS ts_nanos'}, "
                f"level, target, {body} "
                f"FROM logs WHERE id > ? AND ({conds}) ORDER BY id LIMIT 400",
                (self.netlog_last_id, *NETLOG_TARGETS)).fetchall()
            for rid, ts, ts_nanos, level, target, bodyv in rows:
                self._w("netlog", {"log_id": rid, "ts": ts, "ts_nanos": ts_nanos,
                                   "wall_ms": now_ms(), "level": level,
                                   "target": target, "body": (bodyv or "")[:2000]})
                self.netlog_last_id = max(self.netlog_last_id, rid)
            if len(rows) == 400:
                self._err_log("netlog", "busy: draining 400 rows/tick backlog")
        except sqlite3.Error as e:
            self._err_log("netlog", str(e))
        finally:
            c.close()

    # -- thinking (transitory summaries) ------------------------------------------
    def poll_thinking(self) -> None:
        c = _connect_ro(self.thread_history_db)
        if c is None:
            return
        try:
            if not self.thinking_armed:
                # Arm BOTH cursors at the current tail. The old code armed
                # rowid but started updated_at_ordinal at 0, so the first
                # poll replayed up to 200 *weeks-old* reasoning items as if
                # they were brand new on every single engine start.
                rowid = int(c.execute(
                    "SELECT COALESCE(MAX(rowid),0) FROM thread_items").fetchone()[0])
                ord_row = c.execute(
                    "SELECT COALESCE(MAX(updated_at_ordinal),0) FROM thread_items"
                ).fetchone()
                self.thinking_last_rowid = rowid
                self.thinking_last_ord = int(ord_row[0] or 0) if ord_row else 0
                self.thinking_armed = True
                self.log(
                    f"thinking cursor armed at rowid={rowid}, "
                    f"ordinal={self.thinking_last_ord} "
                    f"(only items new/updated from now are captured)", "info")
                return
            rows = c.execute(
                "SELECT rowid, thread_id, turn_id, item_id, created_at_ms, item_json, "
                "item_type, updated_at_ordinal FROM thread_items "
                "WHERE rowid > ? ORDER BY rowid LIMIT 300",
                (self.thinking_last_rowid,)).fetchall()
            # in-place updates bump updated_at_ordinal; rowid never changes
            upd = c.execute(
                "SELECT rowid, thread_id, turn_id, item_id, created_at_ms, item_json, "
                "item_type, updated_at_ordinal FROM thread_items "
                "WHERE item_type='reasoning' AND updated_at_ordinal > ? LIMIT 200",
                (self.thinking_last_ord,)).fetchall()
        except sqlite3.Error as e:
            self._err_log("thinking", str(e))
            c.close()
            return
        c.close()
        merged = {}
        for row in list(rows) + list(upd):
            merged[row[0]] = row
        for rid, row in sorted(merged.items()):
            thread_id, turn_id, item_id, created_ms, item_json, item_type, upd_ord = row[1:]
            self.thinking_last_rowid = max(self.thinking_last_rowid, rid)
            self.thinking_last_ord = max(self.thinking_last_ord, upd_ord or 0)
            try:
                item = json.loads(item_json or "{}")
            except Exception:
                continue
            itype = item.get("type") or item_type
            summaries = item.get("summary") or []
            if itype == "userMessage":
                row = {"ts_ms": now_ms(), "wall": wall_iso(), "kind": "user_message",
                       "thread_id": thread_id, "turn_id": turn_id,
                       "created_at_ms": created_ms,
                       "text": (item.get("text") or "")[:300]}
                self._w("thinking", row)
                if len(self.think_events) < 5000:
                    self.think_events.append(row)
            elif itype == "reasoning":
                key = tuple(summaries)
                prev = self.thinking_seen.get(item_id)
                if summaries and key != prev:
                    row = {"ts_ms": now_ms(), "wall": wall_iso(),
                           "kind": "reasoning_summary", "thread_id": thread_id,
                           "turn_id": turn_id, "item_id": item_id,
                           "created_at_ms": created_ms, "summaries": summaries}
                    self._w("thinking", row)
                    if len(self.think_events) < 5000:
                        self.think_events.append(row)
                elif not summaries and prev:
                    self._w("thinking", {"ts_ms": now_ms(), "wall": wall_iso(),
                                         "kind": "reasoning_cleared", "thread_id": thread_id,
                                         "turn_id": turn_id, "item_id": item_id,
                                         "had_summaries": list(prev)})
                self.thinking_seen[item_id] = key
                if len(self.thinking_seen) > 4000:
                    for k in list(self.thinking_seen)[:1500]:
                        del self.thinking_seen[k]

    # -- usage deltas + spinoffs ----------------------------------------------------
    def poll_usage(self) -> None:
        c = _connect_ro(self.state_db)
        if c is not None:
            try:
                if self.state_cols is None:
                    self.state_cols = {
                        r[1] for r in c.execute("PRAGMA table_info(threads)")}
                if not self.state_cols:
                    self._err_log("usage", "threads table missing; usage poller disabled")
                    self.state_cols = set()
                    self.prev_threads = None  # disables the query below
                if self.prev_threads is not None and "id" in self.state_cols:
                    tok = "tokens_used" if "tokens_used" in self.state_cols \
                        else "NULL AS tokens_used"
                    model = "model" if "model" in self.state_cols \
                        else "NULL AS model"
                    rows = c.execute(f"SELECT id, {tok}, {model} FROM threads").fetchall()
                    th = {r[0]: {"tokens_used": r[1], "model": r[2]} for r in rows}
                    if self.prev_threads:
                        for tid, v in th.items():
                            prev = self.prev_threads.get(tid)
                            if prev is None or prev["tokens_used"] != v["tokens_used"]:
                                self._w("usage", {"ts_ms": now_ms(), "wall": wall_iso(),
                                                  "source": "state_5.threads",
                                                  "thread_id": tid,
                                                  "tokens_used": v["tokens_used"],
                                                  "delta": (None if prev is None else
                                                            (v["tokens_used"] or 0) -
                                                            (prev["tokens_used"] or 0)),
                                                  "model": v["model"]})
                    elif th:
                        # First poll: prime the baseline silently. The old
                        # code re-emitted the entire threads table (100+ rows)
                        # on every engine start.
                        self.log(f"usage baseline primed: {len(th)} threads "
                                 f"(only changes from here are captured)", "info")
                    self.prev_threads = th
            except sqlite3.Error as e:
                self._err_log("usage", str(e))
            finally:
                c.close()
        c = _connect_ro(self.catalog_db)
        if c is not None:
            try:
                spins = c.execute(
                    "SELECT thread_id, display_title, observation_sequence, "
                    "source_created_at FROM local_thread_catalog "
                    "WHERE thread_source='agent_created_thread'").fetchall()
                d = {r[0]: {"title": r[1], "obs_seq": r[2], "created": r[3]} for r in spins}
                if self.prev_spins:
                    for tid, v in d.items():
                        prev = self.prev_spins.get(tid)
                        if prev is None:
                            row = {"ts_ms": now_ms(), "wall": wall_iso(),
                                   "source": "spinoff_thread", "thread_id": tid,
                                   "title": v["title"], "obs_seq": v["obs_seq"],
                                   "event": "spawned"}
                            self._w("usage", row)
                            self.spin_events.append(row)
                            self.log(f"spinoff thread spawned: {v['title']}", "ok")
                        elif prev["obs_seq"] != v["obs_seq"]:
                            self._w("usage", {"ts_ms": now_ms(), "wall": wall_iso(),
                                              "source": "spinoff_thread", "thread_id": tid,
                                              "title": v["title"], "obs_seq": v["obs_seq"],
                                              "event": "activity"})
                elif d:
                    self.log(f"spinoff baseline primed: {len(d)} agent thread(s) "
                             f"(only new spawns from here are captured)", "info")
                self.prev_spins = d
                seq = c.execute(
                    "SELECT host_id, observation_sequence FROM "
                    "local_thread_catalog_sync_state").fetchall()
                for hid, s in seq:
                    s_i, p_i = _to_int(s), _to_int(self.prev_seq.get(hid))
                    if p_i is not None and s_i is not None and p_i != s_i:
                        self._w("usage", {"ts_ms": now_ms(), "wall": wall_iso(),
                                          "source": "codex-dev.sync_state",
                                          "host_id": hid,
                                          "observation_sequence": s_i,
                                          "seq_delta": s_i - p_i})
                    self.prev_seq[hid] = s
            except sqlite3.Error as e:
                self._err_log("usage", str(e))
            finally:
                c.close()

    # -- bytes -----------------------------------------------------------------------
    def poll_bytes(self) -> None:
        try:
            totals = iface_bytes()
        except Exception as e:
            self._err_log("bytes", f"{type(e).__name__}: {e}")
            return
        if totals is None:
            return
        rx, tx = totals
        if self.prev_bytes is not None:
            dr = max(0, rx - self.prev_bytes[0])
            ds = max(0, tx - self.prev_bytes[1])
            self._w("bytes", {"ts_ms": now_ms(), "wall": wall_iso(),
                              "down": dr, "up": ds,
                              "cum_down": rx, "cum_up": tx})
        self.prev_bytes = (rx, tx)

    # -- tick (called by the worker thread, ~10x/s) -----------------------------------
    def tick(self) -> None:
        t = time.time()
        if t >= self._next_due["attach"]:
            self._next_due["attach"] = t + POLL_MS["attach"] / 1000.0
            self.scan_rollouts()
        if t >= self._next_due["rollout"]:
            self._next_due["rollout"] = t + POLL_MS["rollout"] / 1000.0
            self.poll_rollouts()
        if t >= self._next_due["netlog"]:
            self._next_due["netlog"] = t + POLL_MS["netlog"] / 1000.0
            self.poll_netlog()
        if t >= self._next_due["thinking"]:
            self._next_due["thinking"] = t + POLL_MS["thinking"] / 1000.0
            self.poll_thinking()
        if t >= self._next_due["usage"]:
            self._next_due["usage"] = t + POLL_MS["usage"] / 1000.0
            self.poll_usage()
        if t >= self._next_due["bytes"]:
            self._next_due["bytes"] = t + POLL_MS["bytes"] / 1000.0
            self.poll_bytes()
        self._flush()

    def close(self) -> None:
        self._flush()
        for fh in self._fh.values():
            try:
                fh.close()
            except Exception:
                pass
        for t in self.rollout_tails.values():
            try:
                t["fh"].close()
            except Exception:
                pass
        self._fh.clear()
        self.rollout_tails.clear()

    # -- UI snapshot (rendered on the GUI thread via signal) ---------------------------
    def counter_snapshot(self) -> dict:
        rate = "rate limit: —"
        if self.token_events:
            last = self.token_events[-1]
            rate = (f"rate limit: {last.get('used_percent')}% used "
                    f"({last.get('window_minutes')}m window, "
                    f"plan {last.get('plan_type')})")
        ttft = (f"last TTFT: {self._last_ttft_ms} ms ({self._last_ttft_model})"
                if self._last_ttft_ms is not None else "last TTFT: —")
        return {"counts": dict(self.counts), "rate": rate, "ttft": ttft}

    # -- session report -----------------------------------------------------------------
    def write_session_md(self, path: Path) -> Path:
        lines = ["# Live Trap Session", "",
                 f"- started: {wall_iso(self.started_ms)}",
                 f"- stopped: {wall_iso()}",
                 f"- mode: {'LIVE (streams written)' if self.live else 'AUDIT (dry run, no streams)'}",
                 f"- stream rows: {json.dumps(self.counts)}", ""]
        lines += ["## Turns & TTFT", "",
                  "| turn | model | live TTFT | app TTFT | duration |",
                  "|---|---|---|---|---|"]
        for r in self.ttft_events:
            if r.get("event") == "task_complete":
                lines.append(f"| {str(r.get('turn_id'))[:13]} | {r.get('model')} | "
                             f"{r.get('live_ttft_ms')} ms | {r.get('app_ttft_ms')} ms | "
                             f"{r.get('duration_ms')} ms |")
        lines += ["", "## Token / rate-limit events", "",
                  "| time | turn | in | out | used% | window | resets |",
                  "|---|---|---|---|---|---|---|"]
        for r in self.token_events[-200:]:
            lines.append(f"| {r['wall'][11:]} | {str(r.get('turn_id'))[:13]} | "
                         f"{r.get('input_tokens')} | {r.get('output_tokens')} | "
                         f"{r.get('used_percent')} | {r.get('window_minutes')}m | "
                         f"{r.get('resets_at')} |")
        lines += ["", "## Transitory reasoning (live-only captures)", ""]
        for r in self.think_events:
            if r.get("kind") == "reasoning_summary":
                for s in r.get("summaries") or []:
                    lines.append(f"- `{r['wall'][11:19]}` {s}")
        if self.spin_events:
            lines += ["", "## Spinoff threads (agent_created_thread)", ""]
            for r in self.spin_events:
                lines.append(f"- `{r['wall'][11:19]}` {r.get('title')} — {str(r.get('thread_id'))[:13]}")
        lines += ["", "## Notes", "",
                  "- All data read-only from ~/.codex; streams in the output directory.",
                  "- netlog rows carry the app's own nanosecond network telemetry.",
                  "- Reasoning summaries are captured live; rollouts receive them ~3.6s later",
                  "  inside item_completed events (verified on this machine).", ""]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
        return path


# ------------------------------------------------------------------ worker ---

class TrapWorker(QThread):
    """Runs the TrapEngine on its own thread so no poll can block the GUI.

    The worker owns a ~100 ms heartbeat; the engine decides per-poller what
    is due. Log lines and counter snapshots cross to the GUI thread as queued
    signals — the engine itself never touches Qt widgets.
    """

    log = pyqtSignal(str, str)      # message, level
    counters = pyqtSignal(object)   # engine.counter_snapshot() dict
    crashed = pyqtSignal(str)       # fatal engine error

    TICK_S = 0.100
    STOP_JOIN_MS = 4000

    def __init__(self, out_dir: Path, live: bool, codex_root: Path, parent=None):
        super().__init__(parent)
        self.out_dir = Path(out_dir)
        self.live = live
        self.codex_root = Path(codex_root)
        self.engine: TrapEngine | None = None
        self._stop = False

    def run(self) -> None:  # worker thread
        try:
            engine = TrapEngine(self.out_dir, self.live, self.log.emit,
                                self.codex_root)
        except Exception as exc:  # bad out dir etc.
            self.crashed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.engine = engine
        try:
            added = engine.scan_rollouts()
            self.log.emit(f"attached {added} rollout file(s) — new sessions "
                          f"launched from now on are picked up automatically "
                          f"(pre-existing files tailed from EOF)", "info")
            next_due = time.monotonic()
            while not self._stop:
                engine.tick()
                self.counters.emit(engine.counter_snapshot())
                next_due += self.TICK_S
                # Sleep in small slices so stop() stays responsive.
                while not self._stop:
                    remaining = next_due - time.monotonic()
                    if remaining <= 0.005:
                        break
                    time.sleep(min(0.01, max(remaining, 0.001)))
                if next_due < time.monotonic():
                    next_due = time.monotonic()
        except Exception as exc:
            self.crashed.emit(f"{type(exc).__name__}: {exc}")
        finally:
            engine.close()

    def stop(self) -> None:
        """Request stop and join. Bounded: usually <1 s (polls are now
        cheap and no longer spawn processes), hard-capped at STOP_JOIN_MS."""
        self._stop = True
        self.wait(self.STOP_JOIN_MS)


# ------------------------------------------------------------------ page -----

class LiveTrapPage(QWidget):
    mode_changed = pyqtSignal(str)   # "audit" | "live" | "off"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: TrapWorker | None = None
        # Overridable Codex root (tests point this at a fixture).
        self._codex_root_override: Path | None = None

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        inner = QWidget(scroll)
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)

        title = QLabel("Live Tracking", inner)
        title.setProperty("h1", "true")
        layout.addWidget(title)

        self.mode_label = QLabel("MODE: OFF — no interception", inner)
        self.mode_label.setProperty("h1", "true")
        self.mode_label.setStyleSheet("color:#ff5d5d;")
        layout.addWidget(self.mode_label)
        hint = QLabel(
            "Audit Mode (red dot): passive read-only dry run of every poller against "
            "~/.codex — sources verified, cursors armed, events counted, nothing written. "
            "Live Mode (green dot): full interception — six JSONL streams written live "
            "(tokens, ttft, thinking, netlog ns-precision, usage, bytes). "
            "All polling runs on a background thread, so the window stays responsive "
            "even while new sessions are being written. "
            "Stop writes the session .md report.", inner)
        hint.setProperty("muted", "true")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # -- output ---------------------------------------------------------- #
        layout.addWidget(SectionChip("Output", "blue"))
        self.out_row = PathRow(str(streamed_dir()), mode="dir", parent=inner)
        self.out_row.setToolTip(rich_tip(
            "Stream directory", "Where tokens/ttft/thinking/netlog/usage/bytes JSONL "
                                "streams and the session report are written. "
                                "Default: Streamed/ inside the DeKodX2 launch folder."))
        row = QHBoxLayout()
        row.addWidget(QLabel("Streams:", inner))
        row.addWidget(self.out_row, 1)
        layout.addLayout(row)
        self.chk_report = QCheckBox("Write session .md report on stop", inner)
        self.chk_report.setChecked(True)
        layout.addWidget(self.chk_report)

        # -- mode buttons ------------------------------------------------------ #
        layout.addWidget(SectionChip("Mode", "amber"))
        btns = QHBoxLayout()
        btns.setSpacing(10)
        self.audit_btn = QPushButton("●  Audit Mode", inner)
        self.audit_btn.setStyleSheet(
            "QPushButton{background:#3a1518;color:#ff5d5d;border:1px solid #ff5d5d;"
            "border-radius:8px;padding:10px 18px;font-weight:700;}"
            "QPushButton:hover{background:#4d1a1e;}"
            "QPushButton:disabled{color:#7a4a4d;border-color:#7a4a4d;}")
        self.audit_btn.setToolTip(rich_tip(
            "Audit Mode", "Red dot. Passive dry run of all pollers against ~/.codex "
                          "(read-only). Verifies sources, arms cursors, counts events, "
                          "writes nothing."))
        self.live_btn = QPushButton("●  Live Mode", inner)
        self.live_btn.setStyleSheet(
            "QPushButton{background:#0f2b1c;color:#3fd68f;border:1px solid #3fd68f;"
            "border-radius:8px;padding:10px 18px;font-weight:700;}"
            "QPushButton:hover{background:#133724;}"
            "QPushButton:disabled{color:#3f6b52;border-color:#3f6b52;}")
        self.live_btn.setToolTip(rich_tip(
            "Live Mode", "Green dot. Full interception: rollout tailing (TTFT), token_count, "
                         "transitory reasoning summaries, ns-precision netlog, usage deltas, "
                         "spinoff threads and byte counters — all written to JSONL live."))
        self.stop_btn = QPushButton("■  Stop & Write Report", inner)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setToolTip(rich_tip(
            "Stop", "Disarms all pollers and writes the session Markdown report "
                    "(if enabled) summarising turns, TTFT, tokens, reasoning and spinoffs."))
        self.audit_btn.clicked.connect(lambda: self._start(False))
        self.live_btn.clicked.connect(lambda: self._start(True))
        self.stop_btn.clicked.connect(self._stop)
        btns.addWidget(self.audit_btn)
        btns.addWidget(self.live_btn)
        btns.addWidget(self.stop_btn)
        layout.addLayout(btns)

        # -- live counters -------------------------------------------------------- #
        layout.addWidget(SectionChip("Live counters", "green"))
        self.counter_label = QLabel(
            "tokens 0 · ttft 0 · thinking 0 · netlog 0 · usage 0 · bytes 0", inner)
        self.counter_label.setProperty("mono", "true")
        layout.addWidget(self.counter_label)
        self.rate_label = QLabel("rate limit: —", inner)
        layout.addWidget(self.rate_label)
        self.last_ttft = QLabel("last TTFT: —", inner)
        layout.addWidget(self.last_ttft)

        # -- console ---------------------------------------------------------------- #
        layout.addWidget(SectionChip("Console", "blue"))
        self.console = ConsoleLog(inner)
        layout.addWidget(self.console, 1)

        scroll.setWidget(inner)
        wrap = QVBoxLayout(self)
        wrap.setContentsMargins(0, 0, 0, 0)
        wrap.addWidget(scroll)

    # -- control ------------------------------------------------------------------ #
    def log(self, msg: str, level: str = "info") -> None:
        self.console.append_log(msg, level)

    def _codex_root(self) -> Path:
        if self._codex_root_override is not None:
            return Path(self._codex_root_override)
        return CODEX_DIR

    def _start(self, live: bool) -> None:
        if self._worker is not None:
            self.log("already running — stop first", "warn")
            return
        root = self._codex_root()
        if not root.is_dir():
            self.log(f"no Codex data dir at {root}", "error")
            return
        out = Path(self.out_row.edit.text() or str(streamed_dir()))
        mode = "LIVE — intercepting Codex (read-only, streams ON)" if live else \
               "AUDIT — passive dry run (read-only, no writes)"
        color = "#3fd68f" if live else "#ff5d5d"
        self.mode_label.setText(f"MODE: {mode}")
        self.mode_label.setStyleSheet(f"color:{color};")
        self.log(f"engine start: {mode}", "ok")
        self.log(f"sources: sessions={(root / 'sessions').is_dir()} "
                 f"logs_db={(root / 'logs_2.sqlite').is_file()} "
                 f"state_db={(root / 'state_5.sqlite').is_file()} "
                 f"catalog={(root / 'sqlite' / 'codex-dev.db').is_file()} "
                 f"thread_history={(root / 'thread_history_1.sqlite').is_file()}", "info")

        self._worker = TrapWorker(out, live, root, self)
        self._worker.log.connect(self.log)
        self._worker.counters.connect(self._on_counters)
        self._worker.crashed.connect(lambda msg: self.log(f"engine crashed: {msg}", "error"))
        self.audit_btn.setEnabled(False)
        self.live_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self._worker.start()
        self.mode_changed.emit("live" if live else "audit")

    def _on_counters(self, snap: dict) -> None:
        c = snap["counts"]
        self.counter_label.setText(
            f"tokens {c['tokens']} · ttft {c['ttft']} · thinking {c['thinking']} · "
            f"netlog {c['netlog']} · usage {c['usage']} · bytes {c['bytes']}")
        self.rate_label.setText(snap["rate"])
        self.last_ttft.setText(snap["ttft"])

    def _stop(self) -> None:
        w = self._worker
        if w is None:
            return
        self._worker = None
        self.log("engine stopping…", "warn")
        w.stop()  # bounded join; the thread is no longer touching anything
        engine = w.engine
        self.log("engine stopped", "warn")
        if engine is not None and self.chk_report.isChecked():
            try:
                day = datetime.now().strftime("%Y%m%d_%H%M%S")
                p = engine.write_session_md(engine.out_dir / f"trap_session_{day}.md")
                self.log(f"session report: {p}", "ok")
            except Exception as e:
                self.log(f"report failed: {e}", "error")
        self.mode_label.setText("MODE: OFF — no interception")
        self.mode_label.setStyleSheet("color:#ff5d5d;")
        self.audit_btn.setEnabled(True)
        self.live_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.mode_changed.emit("off")

    def shutdown(self) -> None:
        """App close: stop engine, write report if a session was live."""
        if self._worker is not None:
            self._stop()
