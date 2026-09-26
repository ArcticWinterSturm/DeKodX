"""Refactored rollout auditor — uses v2's scanner/databases infrastructure.

Produces per-thread, per-response, per-turn analytics from ~/.codex.
Desktop-specific: logs_2 tracing, session_index.jsonl, models_cache.json.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import configs, databases, scanner
from .analytics import TrackEventsRequest
from .models import SessionRecord
from .redaction import Redactor
from .transcripts import parse_transcript

ROLLOUT_GLOBS = (
    "sessions/**/*.jsonl",
    "sessions/**/*.jsonl.zst",
    "archived_sessions/*.jsonl",
    "trim_archives/**/*.jsonl",
)


def _uuidv7_millis(s: str) -> int | None:
    """Extract the millisecond timestamp from a UUIDv7 string."""
    try:
        return int(str(s).replace("-", "")[:12], 16)
    except Exception:
        return None


def _iso(ms: int | str | None, utc: bool = False) -> str:
    """Convert milliseconds-since-epoch or ISO string to ISO string."""
    if not ms:
        return "?"
    if isinstance(ms, str):
        # Already an ISO string — just format it cleanly
        s = ms.replace("T", " ").replace("Z", " UTC")
        if "." in s:
            s = s.split(".")[0] + " UTC" if utc else s.split(".")[0]
        return s
    d = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return d.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + ("Z" if utc else "")


def _human(n: float) -> str:
    """Human-readable byte size."""
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024 or unit == "TiB":
            return f"{n:,.1f} {unit}" if unit != "B" else f"{int(n):,} B"
        n /= 1024
    return f"{n:,.1f} TiB"


def _fnv1a64(text: str) -> int:
    """FNV-1a 64-bit — used for OTel thread sampling (h % 100 == 0)."""
    h = 0xCBF29CE484222325
    for b in text.encode():
        h = ((h ^ b) * 0x100000001B3) & (2**64 - 1)
    return h


UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _thread_id_of(path: Path) -> str:
    """Extract UUID from rollout filename."""
    m = UUID_RE.search(path.name)
    return m.group(0) if m else path.stem


class RolloutScanner:
    """Scan a single rollout JSONL file, producing per-turn analytics."""

    def __init__(self, path: Path, zst: bool = False):
        self.path = path
        self.zst = zst
        self.lines = 0
        self.bytes_ = 0
        self.by_type: dict[str, int] = {}
        self.blobs = 0
        self.blob_bytes = 0
        self.encrypted = 0
        self.encrypted_bytes = 0
        self.turn_bytes: dict[Optional[str], int] = {}
        self.turn_first: dict[Optional[str], str] = {}
        self.turn_ctx: dict[Optional[str], dict] = {}
        self.responses: list[dict] = []
        self.rate_events: list[dict] = []
        self.item_completed: list[dict] = []
        self.ctx_windows: set[int] = set()
        self.nickname = None
        self.role = None
        self.thread_source = None
        self.root_turn_id = None

    def scan(self) -> Optional[dict]:
        """Parse the rollout file and return analytics dict, or None if empty."""
        if self.zst:
            import io
            import zstandard
            dctx = zstandard.ZstdDecompressor()
            def _open():
                return io.TextIOWrapper(dctx.stream_reader(self.path.open("rb")), "utf-8", errors="replace")
        else:
            def _open():
                return self.path.open("r", encoding="utf-8", errors="replace")

        cur_turn: Optional[str] = None
        with _open() as fh:
            for raw in fh:
                if isinstance(raw, (bytes, bytearray)):
                    try:
                        raw = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        continue
                line = raw.strip()
                if not line:
                    continue
                self.lines += 1
                self.bytes_ += len(line) + 1
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue

                p = rec.get("payload") or {}
                t = rec.get("type")
                pt = p.get("type") if isinstance(p, dict) else None
                key = f"{t}/{pt}" if pt else str(t)
                self.by_type[key] = self.by_type.get(key, 0) + 1

                tid = None
                if isinstance(p, dict):
                    tid = p.get("turn_id")
                if not tid:
                    tid = cur_turn
                else:
                    cur_turn = tid

                self.turn_bytes[tid] = self.turn_bytes.get(tid, 0) + len(line) + 1
                ts = rec.get("timestamp") or ""
                self.turn_first.setdefault(tid, ts)

                self._handle_record(t, p, ts, tid)

        if not self.lines:
            return None
        return self._result()

    def _handle_record(self, t: str, p: dict, ts: str, tid: Optional[str]) -> None:
        """Dispatch a single rollout record."""
        if not isinstance(p, dict):
            return

        if t == "session_meta":
            self.thread_source = p.get("thread_source") or self.thread_source
            self.root_turn_id = p.get("root_turn_id") or self.root_turn_id
        elif t == "turn_context":
            self.turn_ctx[tid] = {
                "ts": ts,
                "model": p.get("model"),
                "effort": p.get("effort"),
                "summary": p.get("summary"),
                "cwd": p.get("cwd"),
                "root_turn_id": p.get("root_turn_id"),
                "collab": (p.get("collaboration_mode") or {}).get("mode")
                if isinstance(p.get("collaboration_mode"), dict) else None,
            }
        elif t == "response_item":
            if p.get("type") == "reasoning":
                summary = p.get("summary") or []
                self.blobs += len(summary)
                self.blob_bytes += sum(len(str(x)) for x in summary)
            for k in ("content", "output"):
                v = p.get(k)
                if isinstance(v, str) and "encrypted_content" in v:
                    self.encrypted += 1
                    self.encrypted_bytes += len(v)
                elif isinstance(v, list):
                    for e in v:
                        if isinstance(e, dict) and e.get("type") == "encrypted_content":
                            self.encrypted += 1
                            self.encrypted_bytes += len(str(e.get("data") or ""))
        elif t == "event_msg":
            etype = p.get("type")
            if etype == "token_count":
                info = p.get("info") or {}
                win = (info or {}).get("model_context_window")
                if win:
                    self.ctx_windows.add(int(win))
                rl = p.get("rate_limits") or {}
                for wname in ("primary", "secondary"):
                    if not isinstance(rl.get(wname), dict):
                        continue
                    w = rl[wname]
                    self.rate_events.append({
                        "ts": ts, "window": wname,
                        "limit_id": rl.get("limit_id"),
                        "used_percent": w.get("used_percent"),
                        "window_minutes": w.get("window_minutes"),
                        "resets_at": w.get("resets_at"),
                        "plan_type": rl.get("plan_type"),
                        "context_window": win,
                        "total": ((info or {}).get("total_token_usage") or {}).get("total_tokens"),
                        "last_token_usage": (info or {}).get("last_token_usage"),
                    })
            elif etype == "item_completed" and p.get("started_at_ms") is not None:
                self.item_completed.append({
                    "ts": ts, "turn_id": tid,
                    "item_type": (p.get("item") or {}).get("type") or p.get("item_type"),
                    "started_at_ms": p.get("started_at_ms"),
                    "duration_ms": p.get("duration_ms"),
                    "completed_at_ms": p.get("completed_at_ms"),
                    "wall_time_ms": (p.get("completed_at_ms") or 0) - (p.get("started_at_ms") or 0),
                })
        elif t == "token_usage_record":
            u = p.get("usage") or {}
            self.responses.append({
                "ts": ts, "turn_id": tid, "response_id": p.get("response_id"),
                "input_tokens": u.get("input_tokens"),
                "cached_input_tokens": u.get("cached_input_tokens"),
                "cache_write_input_tokens": u.get("cache_write_input_tokens"),
                "output_tokens": u.get("output_tokens"),
                "reasoning_output_tokens": u.get("reasoning_output_tokens"),
                "total_tokens": u.get("total_tokens"),
                "budget_units": u.get("codex_rollout_budget_units"),
                "turn_usage": (p.get("turn_token_usage") or {}).get("total_tokens"),
            })

    def _result(self) -> dict:
        return {
            "path": self.path,
            "lines": self.lines,
            "bytes": self.bytes_,
            "by_type": self.by_type,
            "blobs": self.blobs,
            "blob_bytes": self.blob_bytes,
            "encrypted": self.encrypted,
            "encrypted_bytes": self.encrypted_bytes,
            "turn_bytes": self.turn_bytes,
            "turn_first": self.turn_first,
            "turn_ctx": self.turn_ctx,
            "responses": self.responses,
            "rate_events": self.rate_events,
            "items": self.item_completed,
            "ctx_windows": self.ctx_windows,
            "nickname": self.nickname,
            "role": self.role,
            "thread_source": self.thread_source,
            "root_turn_id": self.root_turn_id,
            "zst": self.zst,
        }


class AnalyticsAudit:
    """Full audit combining rollouts, SQLite state, logs_2, session_index, and analytics capture."""

    def __init__(self, home: Path):
        self.home = Path(home)
        self.scanner = scanner
        self.redactor = Redactor(level="standard")

    def run(self, logs: bool = False, grep: str = None, since: str = None,
            quiet_state: bool = False) -> dict:
        """Run the full audit and return a results dict."""
        # 1. Scan rollouts
        rollout_files = self._enumerate_rollouts()
        scanned: dict[str, dict] = {}
        for tid, p, z in rollout_files:
            try:
                rs = RolloutScanner(p, z)
                result = rs.scan()
                if result:
                    scanned[tid] = result
            except Exception as exc:
                print(f"note: could not parse {p.name}: {type(exc).__name__}: {exc}", file=__import__('sys').stderr)

        total_bytes = sum(s["bytes"] for s in scanned.values())

        # 2. Load thread meta from state_5
        threads, edges, names = self._load_thread_meta()

        # 3. DB stats
        db_stats = self._db_stats(verbose=not quiet_state)

        # 4. Model catalog defaults
        model_defaults = self._model_defaults()

        # 5. logs_2 query
        logs_data = []
        if logs:
            logs_data = self._query_logs(grep, since)

        # 6. Build thread summaries
        thread_summaries = self._build_thread_summaries(scanned, threads, edges, names)

        # 7. Build response ledger
        response_ledger = self._build_response_ledger(scanned, threads)

        # 8. Build turn size table
        turn_sizes = self._build_turn_sizes(scanned)

        # 9. Build rate snapshots
        rate_snapshots = self._build_rate_snapshots(scanned)

        # 10. Build latency table
        latency_table = self._build_latency_table(scanned)

        # 11. Inference attempts
        inference = self._build_inference_attempts(scanned)

        return {
            "home": str(self.home),
            "totals": {
                "rollout_bytes": total_bytes,
                "rollouts": len(scanned),
                "found": len(rollout_files),
            },
            "db_stats": db_stats,
            "thread_summaries": thread_summaries,
            "response_ledger": response_ledger,
            "turn_sizes": turn_sizes,
            "rate_snapshots": rate_snapshots,
            "latency_table": latency_table,
            "inference_attempts": inference,
            "model_defaults": model_defaults,
            "logs": logs_data,
            "threads": threads,
            "edges": edges,
            "names": names,
            "scanned": scanned,
        }

    def _enumerate_rollouts(self) -> list[tuple[str, Path, bool]]:
        """Enumerate all rollout files."""
        seen, out = set(), []
        for pat in ROLLOUT_GLOBS:
            for p in sorted(self.home.glob(pat)):
                if p in seen:
                    continue
                seen.add(p)
                out.append((_thread_id_of(p), p, False))
        for p in sorted(self.home.glob("trim_archives/**/*.jsonl.zst")):
            if p not in seen:
                out.append((_thread_id_of(p), p, True))
        return out

    def _load_thread_meta(self) -> tuple[dict, dict, dict]:
        """Load state_5.threads + spawn edges + session_index.jsonl."""
        threads: dict[str, dict] = {}
        edges: dict[str, str] = {}

        db_path = self.home / "state_5.sqlite"
        if db_path.exists():
            try:
                conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=5)
                conn.row_factory = sqlite3.Row
                # Get columns
                cur = conn.execute('PRAGMA table_info("threads")')
                cols = {row["name"] for row in cur.fetchall()}
                want = [c for c in (
                    "id", "title", "name", "first_user_message", "model",
                    "reasoning_effort", "agent_nickname", "agent_role", "agent_path",
                    "tokens_used", "archived", "model_provider", "history_mode",
                    "source", "originator", "thread_source", "created_at",
                    "created_at_ms", "updated_at", "cli_version", "sandbox_policy",
                    "approval_mode", "memory_mode", "git_sha", "git_branch"
                ) if c in cols]
                if want:
                    sql = f'SELECT {", ".join(chr(34) + c + chr(34) for c in want)} FROM threads'
                    for row in conn.execute(sql):
                        d = dict(zip(want, row))
                        tid = str(d.get("id") or "")
                        if tid:
                            threads[tid] = d
                # Spawn edges
                cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='thread_spawn_edges'")
                if cur.fetchone():
                    try:
                        edges = {str(a): str(b) for a, b in
                                 conn.execute("SELECT child_thread_id, parent_thread_id FROM thread_spawn_edges")}
                    except sqlite3.Error:
                        pass
                conn.close()
            except sqlite3.Error:
                pass

        # session_index.jsonl
        names: dict[str, str] = {}
        idx = self.home / "session_index.jsonl"
        if idx.exists():
            for line in idx.read_text(errors="replace").splitlines():
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if d.get("id"):
                    names[str(d["id"])] = str(d.get("thread_name") or d.get("name") or "")

        return threads, edges, names

    def _db_stats(self, verbose: bool = True) -> dict:
        """Collect DB stats for state_5, logs_2, etc."""
        stats = {}
        for base in ("state_5", "logs_2"):
            path = self.home / f"{base}.sqlite"
            db_info = {"bytes": 0, "tables": {}, "rows": 0, "note": ""}
            if not path.exists():
                db_info["note"] = "absent"
                stats[base] = db_info
                continue
            db_info["bytes"] = path.stat().st_size
            try:
                conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5)
                conn.row_factory = sqlite3.Row
                db_info["journal"] = conn.execute("PRAGMA journal_mode").fetchone()[0]
                db_info["page_size"] = conn.execute("PRAGMA page_size").fetchone()[0]
                table_names = [r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
                for name in table_names:
                    try:
                        cnt = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                    except sqlite3.Error:
                        cnt = None
                    db_info["tables"][name] = cnt
                    if cnt:
                        db_info["rows"] += cnt
                if base == "logs_2" and "logs" in db_info["tables"]:
                    try:
                        r = conn.execute(
                            "SELECT COUNT(*), SUM(estimated_bytes), MIN(ts), MAX(ts) FROM logs"
                        ).fetchone()
                        db_info["logs"] = {
                            "rows": r[0],
                            "estimated_bytes": r[1],
                            "first_ts": _iso(r[2]),
                            "last_ts": _iso(r[3]),
                        }
                    except sqlite3.Error:
                        pass
                if base == "state_5" and "threads" in db_info["tables"]:
                    try:
                        archived = conn.execute(
                            "SELECT COUNT(*) FROM threads WHERE COALESCE(archived, 0) = 1"
                        ).fetchone()[0]
                        db_info["archived_threads"] = archived
                    except sqlite3.Error:
                        pass
                conn.close()
            except sqlite3.Error as exc:
                db_info["note"] = f"partial: {exc}"
            stats[base] = db_info
        return stats

    def _model_defaults(self) -> dict:
        """Load models_cache.json → model → default effort mapping."""
        out = {}
        for name in ("models_cache.json", "models.json"):
            f = self.home / name
            if not f.exists():
                continue
            try:
                data = json.loads(f.read_text(errors="replace"))
            except Exception:
                continue

            def walk(node):
                if isinstance(node, dict):
                    mid = node.get("slug") or node.get("model") or node.get("id")
                    lvl = (
                        node.get("default_reasoning_level")
                        or node.get("default_reasoning_effort")
                        or (node.get("reasoning") or {}).get("default")
                        if isinstance(node.get("reasoning"), dict)
                        else node.get("default_reasoning_level")
                    )
                    if isinstance(mid, str) and isinstance(lvl, str):
                        out.setdefault(mid, lvl)
                    for v in node.values():
                        walk(v)
                elif isinstance(node, list):
                    for v in node:
                        walk(v)

            walk(data)
        return out

    def _query_logs(self, grep: str = None, since: str = None) -> list[dict]:
        """Query logs_2.sqlite for tracing rows."""
        conn = None
        try:
            conn = sqlite3.connect(
                f"file:{(self.home / 'logs_2.sqlite').as_posix()}?mode=ro", uri=True, timeout=5
            )
            conn.row_factory = sqlite3.Row
            q = "SELECT ts, level, target, SUBSTR(feedback_log_body, 1, 160) AS b FROM logs"
            cond, prm = [], []
            if grep:
                cond.append("(target LIKE ? OR feedback_log_body LIKE ?)")
                prm += [f"%{grep}%", f"%{grep}%"]
            if since:
                cond.append("ts >= ?")
                prm.append(since)
            if cond:
                q += " WHERE " + " AND ".join(cond)
            q += " ORDER BY ts DESC LIMIT 40"
            rows = [dict(r) for r in conn.execute(q, prm)]
            conn.close()
            return rows
        except sqlite3.Error as exc:
            if conn:
                conn.close()
            return [{"error": str(exc)}]

    def _build_thread_summaries(self, scanned, threads, edges, names) -> list[dict]:
        """Build per-thread summary with effort, tokens, source."""
        summaries = []
        for tid, s in scanned.items():
            m = threads.get(tid, {})
            eff = sorted({str(t.get("effort")) for t in s["turn_ctx"].values()})
            eff = [e for e in eff if e != "None"] or ["absent→default"]
            tos = sum(x["output_tokens"] or 0 for x in s["responses"])
            tin = sum(x["input_tokens"] or 0 for x in s["responses"])
            trea = sum(x["reasoning_output_tokens"] or 0 for x in s["responses"])
            win = sorted(s["ctx_windows"])
            r = s["rate_events"]
            prim = [e for e in r if e.get("window") == "primary"] or r
            lastp = prim[-1].get("used_percent") if prim else None

            summaries.append({
                "thread_id": tid,
                "name": names.get(tid) or m.get("title") or "(unnamed)",
                "source": s["thread_source"] or m.get("source") or "user",
                "originator": m.get("originator") or "?",
                "history_mode": m.get("history_mode") or "?",
                "model": sorted({t.get("model") for t in s["turn_ctx"].values() if t.get("model")}),
                "effort": eff,
                "summary": sorted({str(t.get("summary")) for t in s["turn_ctx"].values()} or ["-"]),
                "turns": len(s["turn_ctx"]) or len(set(s["turn_bytes"])) - 1,
                "responses": len(s["responses"]),
                "input_tokens": tin,
                "output_tokens": tos,
                "reasoning_tokens": trea,
                "context_windows": win,
                "used_percent": lastp,
                "limit_id": sorted({x["limit_id"] for x in r if x["limit_id"]}),
                "parent": edges.get(tid),
                "root_turn_id": s["root_turn_id"],
                "created_at_ms": m.get("created_at_ms"),
            })

        summaries.sort(key=lambda x: (
            x["root_turn_id"] is not None,
            x["created_at_ms"] or 0,
        ))
        return summaries

    def _build_response_ledger(self, scanned, threads) -> list[dict]:
        """Build per-response ledger: effort × reasoning tokens per response."""
        ledger = []
        for tid, s in sorted(scanned.items(),
                             key=lambda kv: (kv[1]["root_turn_id"] is not None,
                                             threads.get(kv[0], {}).get("created_at_ms", 0))):
            if not s["responses"]:
                continue
            for i, rr in enumerate(s["responses"], 1):
                eff = (s["turn_ctx"].get(rr["turn_id"]) or {}).get("effort") or "absent→model-default"
                summ = (s["turn_ctx"].get(rr["turn_id"]) or {}).get("summary")
                b = " budget_units=ABSENT(skip_serializing)" if rr["budget_units"] is None else ""
                ledger.append({
                    "thread_id": tid,
                    "response_index": i,
                    "ts": rr["ts"],
                    "effort": eff,
                    "summary": summ,
                    "input_tokens": rr["input_tokens"] or 0,
                    "cached_input_tokens": rr["cached_input_tokens"] or 0,
                    "output_tokens": rr["output_tokens"] or 0,
                    "reasoning_output_tokens": rr["reasoning_output_tokens"] or 0,
                    "budget_note": b,
                })
        return ledger

    def _build_turn_sizes(self, scanned) -> list[dict]:
        """Build per-turn byte size table."""
        sizes = []
        for tid, s in sorted(scanned.items(), key=lambda kv: -kv[1]["bytes"]):
            ts = sorted(
                ((k, v) for k, v in s["turn_bytes"].items() if k),
                key=lambda kv: s["turn_first"].get(kv[0]) or "",
            )
            sizes.append({
                "thread_id": tid,
                "total_bytes": s["bytes"],
                "total_lines": s["lines"],
                "blobs": s["blobs"],
                "blob_bytes": s["blob_bytes"],
                "encrypted": s["encrypted"],
                "encrypted_bytes": s["encrypted_bytes"],
                "turns": len(ts),
                "turn_details": ts[:40],
            })
        return sizes

    def _build_rate_snapshots(self, scanned) -> list[dict]:
        """Build rate-limit snapshot table."""
        rows = []
        for tid, s in scanned.items():
            for ev in s["rate_events"]:
                rows.append({
                    "thread_id": tid,
                    **ev,
                })
        rows.sort(key=lambda r: r.get("ts", ""))
        return rows[:40]

    def _build_latency_table(self, scanned) -> list[dict]:
        """Build item_completed latency table."""
        rows = []
        for tid, s in sorted(scanned.items()):
            for it in s["items"]:
                rows.append({
                    "thread_id": tid,
                    **it,
                })
        return rows[:24]

    def _build_inference_attempts(self, scanned) -> list[dict]:
        """Build inference attempt regression data."""
        attempts = []
        for tid, s in sorted(scanned.items(), key=lambda kv: -kv[1]["bytes"]):
            ev = [e for e in s["rate_events"] if e.get("used_percent") is not None
                  and e.get("window") == "primary"]
            if len(ev) >= 2:
                d_pct = ev[-1]["used_percent"] - ev[0]["used_percent"]
                if d_pct > 0:
                    d_tok = sum(r["total_tokens"] or 0 for r in s["responses"])
                    attempts.append({
                        "thread_id": tid,
                        "delta_pct": d_pct,
                        "delta_tokens": d_tok,
                        "window_100pct_approx": d_tok * 100 / d_pct if d_pct else 0,
                    })
        return attempts

    def format_text(self, data: dict) -> str:
        """Format audit results as human-readable text."""
        lines = []
        lines.append("# Codex Local-State Audit")
        lines.append(f"  home: {data['home']}")
        lines.append(f"  rollouts: {data['totals']['rollouts']} readable ({_human(data['totals']['rollout_bytes'])})")
        lines.append("")

        # DB stats
        lines.append("## DB Stats")
        for base, st in data["db_stats"].items():
            tabs = ", ".join(f"{k}={v}" for k, v in sorted(st["tables"].items(), key=lambda kv: -(kv[1] or 0))[:4])
            lines.append(f"  {base}.sqlite: {_human(st['bytes'])} — {tabs}")
        lines.append("")

        # Thread summaries
        lines.append("## 1. Threads: family, effort, tokens")
        for ts in data["thread_summaries"]:
            eff_str = ",".join(ts["effort"]) or "?"
            model_str = ",".join(ts["model"]) or "?"
            parent_str = f" ↳ spawned by {ts['parent'][:8]}" if ts["parent"] else ""
            pct = f"{ts['used_percent']:.1f}" if ts["used_percent"] is not None else "n/a"
            lines.append(
                f"  {ts['thread_id'][:8]}  {ts['name'][:28]:28s}  src={ts['source']:<6} "
                f"model={model_str[:13]:14s} effort={eff_str:<9} "
                f"turns={ts['turns']:3d} resp={ts['responses']:3d} "
                f"in={ts['input_tokens']:7d} out={ts['output_tokens']:7d} "
                f"reason={ts['reasoning_tokens']:7d}  meter={pct}%{parent_str}"
            )
        lines.append("")

        # Response ledger
        lines.append("## 2. Per-response reasoning ledger")
        for thread_id in sorted(set(r["thread_id"] for r in data["response_ledger"])):
            thread_resps = [r for r in data["response_ledger"] if r["thread_id"] == thread_id]
            lines.append(f"  {thread_id[:8]}  ({len(thread_resps)} responses)")
            for rr in thread_resps:
                lines.append(
                    f"    #{rr['response_index']:<3} effort={rr['effort']:<7} "
                    f"in={rr['input_tokens']:<7} cache={rr['cached_input_tokens']:<7} "
                    f"out={rr['output_tokens']:<6} REASON={rr['reasoning_output_tokens']:<6}{rr['budget_note']}"
                )
        lines.append("")

        # Turn sizes
        lines.append("## 3. Turn size on disk (JSONL), per thread")
        for ts in data["turn_sizes"]:
            lines.append(
                f"  {ts['thread_id'][:8]}  {_human(ts['total_bytes']):>9}  {ts['total_lines']:>6} lines"
                f"  blobs: {ts['blobs']:3d} ({_human(ts['blob_bytes'])}), "
                f"encrypted: {ts['encrypted']} ({_human(ts['encrypted_bytes'])})"
            )
        lines.append("")

        # Rate snapshots
        lines.append("## 4. Rate-limit snapshots")
        for rs in data["rate_snapshots"]:
            lines.append(
                f"  {rs.get('ts', '?')[:23]} {rs['thread_id'][:8]} {rs.get('window', '?'):<9} "
                f"id={str(rs.get('limit_id', '?')):<14} used={rs.get('used_percent', '?')}% "
                f"win={rs.get('window_minutes', '?')}min"
            )
        lines.append("")

        # Latency
        lines.append("## 5. Item latencies")
        for lt in data["latency_table"]:
            lines.append(
                f"  {lt.get('ts', '?')[:19]} {lt['thread_id'][:8]} "
                f"{str(lt.get('item_type', '?')):<24} wall={lt.get('wall_time_ms', '?'):>7}ms"
            )
        lines.append("")

        # Inference
        lines.append("## 6. Inference attempts (naive linear regression)")
        for ia in data["inference_attempts"]:
            lines.append(
                f"  {ia['thread_id'][:8]}: +{ia['delta_pct']:.2f}% against +{ia['delta_tokens']:,} tokens "
                f"→ 100% ≈ {ia['window_100pct_approx']:,.0f} tokens"
            )
        lines.append("")

        # Model defaults
        if data["model_defaults"]:
            lines.append("## 2c. Model catalog default effort")
            for k, v in sorted(data["model_defaults"].items())[:12]:
                lines.append(f"    {k:<28} default_reasoning_level = {v}")
            lines.append("")

        # FNV sampling
        lines.append("## 2d. OTel thread sampling (FNV-1a % 100 == 0)")
        for ts in data["thread_summaries"]:
            tid = ts["thread_id"]
            h = _fnv1a64(tid)
            lines.append(f"    {tid}  h%100={h % 100:>2}  {'SAMPLED' if h % 100 == 0 else 'not emitted'}")
        lines.append("")

        # Logs
        if data["logs"]:
            lines.append("## 7. logs_2 tracing rows")
            for row in data["logs"]:
                if "error" in row:
                    lines.append(f"  error: {row['error']}")
                else:
                    lines.append(f"  {row.get('ts', '?')} {row.get('level', '?'):<5} {row.get('target', '?')[:38]:38s} {row.get('b', '')}")
            lines.append("")

        return "\n".join(lines)


def run_audit(home: str | Path, logs: bool = False, grep: str = None,
              since: str = None, quiet_state: bool = False,
              output_json: bool = False) -> str:
    """Run the audit and return formatted text or JSON."""
    audit = AnalyticsAudit(Path(home))
    data = audit.run(logs=logs, grep=grep, since=since, quiet_state=quiet_state)

    if output_json:
        # Convert scanned to serializable form
        serializable = {k: v for k, v in data.items() if k != "scanned"}
        serializable["scanned"] = {tid: {k: v for k, v in s.items() if k != "path"}
                                    for tid, s in data["scanned"].items()}
        serializable["scanned"] = {tid: {**s, "path": str(data["scanned"][tid]["path"])}
                                    for tid, s in data["scanned"].items()}
        return json.dumps(serializable, indent=2, default=str)
    else:
        return audit.format_text(data)
