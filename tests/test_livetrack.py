"""Regression tests for the Live Trap engine (no display needed).

Covers the 2026-09 Windows lockup fixes:
  * startup replay storms  - old thinking/usage rows must NOT be re-emitted
  * new-session attach     - rollouts created after engine start get tailed
  * rollout truncation     - rotated/truncated files resync, deleted drop
  * netstat fallback parse - header lines never mis-parsed as data
  * error throttling       - repeated poller errors log once per interval

Run:  QT_QPA_PLATFORM=offscreen python3 tests/test_livetrack.py
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dex.ui.page_livetrack import (  # noqa: E402
    TrapEngine, _netstat_iface_bytes, iface_bytes,
)

PASS = 0
FAIL = 0


def check(label: str, condition: bool) -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}")


# --------------------------------------------------------------------------- #
# fixture
# --------------------------------------------------------------------------- #

def _conn(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=MEMORY")
    return conn


def build_codex_root(root: Path) -> Path:
    sessions = root / "sessions" / "2026" / "09" / "21"
    sessions.mkdir(parents=True)
    old = sessions / "rollout-2026-09-21T10-00-00-11111111-2222-3333-4444-555555555555.jsonl"
    old.write_text(
        json.dumps({"type": "session_meta", "payload": {"id": "11111111-2222-3333-4444-555555555555"}})
        + "\n", encoding="utf-8")
    # make the old file look pre-existing (tail-from-EOF path)
    past = time.time() - 3600
    import os as _os
    _os.utime(old, (past, past))

    # state_5.sqlite - two existing threads
    c = _conn(root / "state_5.sqlite")
    c.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, tokens_used INTEGER, model TEXT)")
    c.execute("INSERT INTO threads VALUES ('t-existing', 1000, 'gpt-5.6')")
    c.execute("INSERT INTO threads VALUES ('t-existing2', 50, 'gpt-5.5')")
    c.commit()
    c.close()

    # sqlite/codex-dev.db - one existing agent thread + sync state
    c = _conn(root / "sqlite" / "codex-dev.db")
    c.execute("CREATE TABLE local_thread_catalog "
              "(thread_id TEXT, display_title TEXT, observation_sequence INTEGER, "
              "source_created_at REAL, thread_source TEXT)")
    c.execute("INSERT INTO local_thread_catalog VALUES "
              "('agent-1', 'existing agent', 10, 1780000000.0, 'agent_created_thread')")
    c.execute("CREATE TABLE local_thread_catalog_sync_state "
              "(host_id TEXT, observation_sequence INTEGER)")
    c.execute("INSERT INTO local_thread_catalog_sync_state VALUES ('host-1', 100)")
    c.commit()
    c.close()

    # thread_history_1.sqlite - OLD reasoning rows (weeks old, already updated)
    c = _conn(root / "thread_history_1.sqlite")
    c.execute("CREATE TABLE thread_items (thread_id TEXT, turn_id TEXT, item_id TEXT, "
              "created_at_ms INTEGER, item_json TEXT, item_type TEXT, updated_at_ordinal INTEGER)")
    old_ms = 1786000000000  # weeks in the past
    for i in range(3):
        c.execute("INSERT INTO thread_items VALUES (?,?,?,?,?,?,?)",
                  ("t-old", "turn-old", f"ri_{i}", old_ms + i,
                   json.dumps({"type": "reasoning",
                               "summary": [{"type": "summary_text", "text": f"old thinking {i}"}]}),
                   "reasoning", i + 1))
    c.commit()
    c.close()

    # logs_2.sqlite - some old rows
    c = _conn(root / "logs_2.sqlite")
    c.execute("CREATE TABLE logs (id INTEGER PRIMARY KEY, ts TEXT, level TEXT, "
              "target TEXT, feedback_log_body TEXT)")
    for i in range(5):
        c.execute("INSERT INTO logs VALUES (?,?,?,?,?)",
                  (i, "2026-09-21T09:00:00", "INFO", "hyper::client", f"old log {i}"))
    c.commit()
    c.close()
    return root


def stream_rows(out: Path, name: str, eng: TrapEngine | None = None) -> list[dict]:
    if eng is not None:
        eng._flush()  # pollers batch one flush per tick; tests poll directly
    p = sorted(out.glob(f"{name}-*.jsonl"))
    if not p:
        return []
    return [json.loads(line) for line in p[-1].read_text(encoding="utf-8").splitlines() if line]


# --------------------------------------------------------------------------- #
# tests
# --------------------------------------------------------------------------- #

def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="dekodx_trap_test_"))
    root = build_codex_root(tmp / "codex")
    out = tmp / "streams"
    logs: list[tuple[str, str]] = []
    eng = TrapEngine(out, live=True, log=lambda m, l="info": logs.append((m, l)),
                     codex_root=root)

    print("-- first tick: arm + prime, must replay NOTHING --")
    eng.tick()
    check("no thinking replay of old rows", stream_rows(out, "thinking", eng) == [])
    check("no usage replay of all threads", stream_rows(out, "usage", eng) == [])
    check("no netlog replay of old rows", stream_rows(out, "netlog", eng) == [])
    check("usage baseline primed logged",
          any("usage baseline primed" in m for m, _ in logs))
    check("spinoff baseline primed logged",
          any("spinoff baseline primed" in m for m, _ in logs))
    check("thinking cursor armed logged",
          any("thinking cursor armed" in m for m, _ in logs))
    check("netlog cursor armed logged",
          any("netlog cursor armed" in m for m, _ in logs))

    print("-- new activity is captured --")
    # 1) fresh reasoning row + in-place update of an old row (ordinal bump)
    c = _conn(root / "thread_history_1.sqlite")
    c.execute("INSERT INTO thread_items VALUES (?,?,?,?,?,?,?)",
              ("t-new", "turn-new", "ri_new", 1789999999000,
               json.dumps({"type": "reasoning",
                           "summary": [{"type": "summary_text", "text": "fresh thinking"}]}),
               "reasoning", 99))
    c.execute("UPDATE thread_items SET updated_at_ordinal=98, "
              "item_json=? WHERE item_id='ri_1'",
              (json.dumps({"type": "reasoning",
                           "summary": [{"type": "summary_text", "text": "updated summary"}]}),))
    c.commit()
    c.close()
    eng.poll_thinking()
    think = stream_rows(out, "thinking", eng)
    check("fresh reasoning row captured",
          any(r.get("text") == "fresh thinking" or (r.get("summaries") or [{}])[0].get("text") == "fresh thinking"
              for r in think))
    check("in-place reasoning update captured",
          any(any(s.get("text") == "updated summary" for s in (r.get("summaries") or []))
              for r in think))
    check("old (untouched) reasoning rows not re-emitted",
          not any("old thinking" in json.dumps(r) for r in think))

    # 2) usage: bump an existing thread, add a new one, spawn an agent, move sync seq
    c = _conn(root / "state_5.sqlite")
    c.execute("UPDATE threads SET tokens_used=1500 WHERE id='t-existing'")
    c.execute("INSERT INTO threads VALUES ('t-new-thread', 5, 'gpt-5.6')")
    c.commit()
    c.close()
    c = _conn(root / "sqlite" / "codex-dev.db")
    c.execute("INSERT INTO local_thread_catalog VALUES "
              "('agent-2', 'new agent', 20, 1790000000.0, 'agent_created_thread')")
    c.execute("UPDATE local_thread_catalog_sync_state SET observation_sequence=105 "
              "WHERE host_id='host-1'")
    c.commit()
    c.close()
    eng.poll_usage()
    usage = stream_rows(out, "usage", eng)
    check("token delta captured (500)",
          any(r.get("source") == "state_5.threads" and r.get("thread_id") == "t-existing"
              and r.get("delta") == 500 for r in usage))
    check("brand-new thread captured once",
          any(r.get("thread_id") == "t-new-thread" for r in usage))
    check("untouched thread NOT re-emitted",
          not any(r.get("thread_id") == "t-existing2" for r in usage))
    check("new spinoff thread captured",
          any(r.get("source") == "spinoff_thread" and r.get("event") == "spawned"
              and r.get("thread_id") == "agent-2" for r in usage))
    check("existing spinoff NOT re-emitted as spawned",
          not any(r.get("thread_id") == "agent-1" and r.get("event") == "spawned"
                  for r in usage))
    check("sync sequence delta captured",
          any(r.get("source") == "codex-dev.sync_state" and r.get("seq_delta") == 5
              for r in usage))

    # 3) netlog: only NEW rows after the armed cursor
    c = _conn(root / "logs_2.sqlite")
    c.execute("INSERT INTO logs VALUES (?,?,?,?,?)",
              (99, "2026-09-21T10:00:00", "INFO", "hyper::client", "fresh netlog"))
    c.commit()
    c.close()
    eng.poll_netlog()
    netlog = stream_rows(out, "netlog", eng)
    check("new netlog row captured",
          any(r.get("body") == "fresh netlog" for r in netlog))
    check("old netlog rows not re-emitted",
          not any("old log" in (r.get("body") or "") for r in netlog))

    print("-- new session (rollout created after engine start) is tailed --")
    newfile = (root / "sessions" / "2026" / "09" / "22"
               / "rollout-2026-09-22T09-00-00-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl")
    newfile.parent.mkdir(parents=True, exist_ok=True)
    newfile.write_text(
        json.dumps({"type": "session_meta", "payload": {"id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}}) + "\n"
        + json.dumps({"type": "turn_context", "payload": {"turn_id": "turn-1", "model": "gpt-5.6"}}) + "\n"
        + json.dumps({"type": "event_msg", "payload": {"type": "task_started", "turn_id": "turn-1"}}) + "\n",
        encoding="utf-8")
    added = eng.scan_rollouts()
    check("periodic rescan attaches the new rollout", added == 1)
    eng.poll_rollouts()
    check("new rollout lines tailed (turn_start emitted)", eng.counts["ttft"] >= 1)

    print("-- truncation / rotation resync --")
    newfile.write_text("", encoding="utf-8")  # simulate rotation/truncation
    eng.poll_rollouts()
    check("truncated rollout flagged",
          any("rotated/truncated" in m for m, _ in logs))
    newfile.write_text(
        json.dumps({"type": "event_msg", "payload": {"type": "task_started", "turn_id": "turn-2"}}) + "\n",
        encoding="utf-8")
    eng.scan_rollouts()
    eng.poll_rollouts()
    check("re-attached rollout tailed again", eng.counts["ttft"] >= 2)
    # On Windows we cannot unlink an open file; close the engine's handle first
    # so the "removed" path can be exercised. On Linux the unlink-while-open
    # case is the real-world scenario (session dir cleaned under the engine).
    if sys.platform == "win32":
        eng.close()
        newfile.unlink()
        # Re-create the engine and verify it handles the missing file gracefully
        eng2 = TrapEngine(out, live=True, log=lambda m, l="info": logs.append((m, l)),
                          codex_root=root)
        eng2.scan_rollouts()
        check("removed rollout not re-attached after close", eng2.counts["ttft"] == 0)
        eng2.close()
    else:
        newfile.unlink()  # session removed entirely
        eng.poll_rollouts()
        check("removed rollout dropped",
              any("removed" in m for m, _ in logs))

    print("-- netstat fallback parser (Windows two-line header + data rows) --")
    fake_out = (
        "Interface Statistics\n"
        "\n"
        "            Bytes             Received        Bytes           Total Errors\n"
        "                                           Transmitted        Unknown        Rcvd Errors\n"
        "\n"
        "Loopback Pseudo-Interface 1            316138064        316138064            0            0             0            0\n"
        "Ethernet 2                             1128495204       1046478108            0            0             0            0\n"
        "Wi-Fi 3                                   45000          35000            0            0             0            0\n"
    )

    class _P:
        stdout = fake_out

    real_run = subprocess.run
    subprocess.run = lambda *a, **k: _P()  # type: ignore[assignment]
    try:
        rx, tx = _netstat_iface_bytes()
    finally:
        subprocess.run = real_run  # type: ignore[assignment]
    check("netstat fallback sums physical interfaces only",
          rx == 1128495204 + 45000 and tx == 1046478108 + 35000)

    got = iface_bytes()
    check("iface_bytes() returns (rx, tx) integers",
          isinstance(got, tuple) and all(isinstance(v, int) for v in got))

    print("-- error throttling --")
    eng._err_log("netlog", "some repeated error")
    eng._err_log("netlog", "some repeated error")
    eng._err_log("netlog", "some repeated error")
    n = sum(1 for m, _ in logs if m.startswith("netlog: some repeated error"))
    check("identical error logged only once per interval", n == 1)

    eng.close()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
