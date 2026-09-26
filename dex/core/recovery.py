"""Recovery orchestrator: wires scanning, parsing, redaction and reporting.

Used by both the Qt worker thread and the headless CLI. Progress is reported
as a 0..1 float plus a human-readable status line; cancellation is polled
through a callable so the UI stays responsive.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Callable, Optional

from . import configs, databases, report, scanner
from .models import (
    RecoveryCancelled, RecoveryOptions, RecoveryResult, SessionRecord, SubagentInfo
)
from .redaction import Redactor
from .transcripts import parse_transcript

LogFn = Callable[[str, str], None]          # (message, level)
ProgressFn = Callable[[float, str], None]   # (0..1, status label)


def _noop_log(msg: str, level: str = "info") -> None:  # pragma: no cover
    pass


def _noop_progress(frac: float, label: str = "") -> None:  # pragma: no cover
    pass


def _check(cancel: Optional[Callable[[], bool]]) -> None:
    if cancel is not None and cancel():
        raise RecoveryCancelled()


def _session_id_from_path(path: Path) -> str:
    stem = path.stem
    match = re.search(r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$", stem)
    return match.group(1) if match else stem


def build_session_list(source: Path, scan_rep: scanner.ScanReport) -> list[dict]:
    """Union of session_index.jsonl and transcripts found on disk."""
    entries: dict[str, dict] = {}
    for row in scan_rep.index_entries:
        sid = str(row.get("id") or "")
        if not sid:
            continue
        entries[sid] = {
            "id": sid,
            "index_title": str(row.get("thread_name") or ""),
            "index_updated": str(row.get("updated_at") or ""),
            "archived": False,
        }
    for path in scan_rep.transcript_files + scan_rep.archived_files:
        sid = _session_id_from_path(path)
        archived = "archived_sessions" in path.as_posix()
        item = entries.setdefault(sid, {"id": sid, "index_title": "", "index_updated": "", "archived": archived})
        item["archived"] = archived or item.get("archived", False)
        if not item.get("index_updated"):
            try:
                import datetime as _dt
                mtime = path.stat().st_mtime
                item["index_updated"] = (
                    _dt.datetime.fromtimestamp(mtime, tz=_dt.timezone.utc)
                    .isoformat().replace("+00:00", "Z")
                )
            except OSError:
                pass
    out = list(entries.values())
    out.sort(key=lambda e: e.get("index_updated") or "")
    return out


def _link_subagents(
    sessions: list[SessionRecord],
    scan_rep: scanner.ScanReport,
) -> None:
    """Link parent sessions to their subagent sessions."""
    # Build lookup by session_id
    by_id: dict[str, SessionRecord] = {s.session_id: s for s in sessions}

    # Also build lookup by parent_thread_id for subagent discovery
    children_of: dict[str, list[SessionRecord]] = {}
    for s in sessions:
        if s.parent_thread_id and s.parent_thread_id in by_id:
            children_of.setdefault(s.parent_thread_id, []).append(s)

    # Wire up parent→child links
    for parent_id, children in children_of.items():
        parent = by_id.get(parent_id)
        if parent is None:
            continue
        for child in children:
            child.parent_session = parent
            info = SubagentInfo(
                child_session_id=child.session_id,
                child_title=child.title,
                child_nickname=child.agent_nickname,
                child_model=child.model,
                child_tokens=child.db_tokens_used or (child.total_tokens.total_tokens if child.total_tokens else 0),
                child_status=child.status,
                task_name=child.agent_role or child.title,
                transcript_path=child.transcript_path,
            )
            parent.subagents.append(info)


def run_recovery(
    source: Path,
    output: Path,
    options: RecoveryOptions,
    log: LogFn = _noop_log,
    progress: ProgressFn = _noop_progress,
    cancel: Optional[Callable[[], bool]] = None,
) -> RecoveryResult:
    source = Path(source)
    output = Path(output)
    result = RecoveryResult(output_path=output)

    # -- step 1: scan -------------------------------------------------------- #
    progress(0.02, "Scanning directory…")
    log(f"Scanning {source}")
    scan_rep = scanner.scan(source)
    if not scan_rep.exists:
        raise ValueError(f"Source directory does not exist: {source}")
    if not scan_rep.looks_like_codex:
        log("Warning: directory does not look like a Codex data dir - continuing anyway", "warn")
    for issue in scan_rep.issues:
        log(issue, "warn")
    log(f"Found {len(scan_rep.index_entries)} index entries, "
        f"{len(scan_rep.transcript_files)} active + {len(scan_rep.archived_files)} archived transcripts")

    # Version info
    result.version = scan_rep.version
    log(f"Detected versions: {scan_rep.version.display_version}")
    if scan_rep.version.config_format:
        log(f"Config format: {scan_rep.version.config_format}")
    if scan_rep.subagent_candidates:
        log(f"Subagent candidates: {len(scan_rep.subagent_candidates)}")

    # -- step 2: configs + secrets ------------------------------------------ #
    _check(cancel)
    progress(0.06, "Reading configuration…")
    cfg = configs.load_toml(source / "config.toml")
    auth = configs.load_json(source / "auth.json")
    gstate = configs.load_json(source / ".codex-global-state.json")
    installation_id = configs.read_text(source / "installation_id", 512)
    cap_sid = configs.read_text(source / "cap_sid", 4096)

    secrets = configs.collect_config_secrets(cfg, auth, installation_id, cap_sid)

    # -- step 3: databases ---------------------------------------------------- #
    _check(cancel)
    progress(0.10, "Reading SQLite databases…")
    state_db = source / "state_5.sqlite"
    threads = databases.read_threads(state_db) if scan_rep.has_state_db else []
    thread_by_id = {str(t.get("id")): t for t in threads if t.get("id")}
    for row in threads:
        url = row.get("git_origin_url")
        if isinstance(url, str) and url.strip():
            secrets.append(url.strip())
    dyn_tools = databases.read_dynamic_tools(state_db) if scan_rep.has_state_db else []
    goals = databases.read_goals_from_state(state_db) if scan_rep.has_state_db else []
    jobs = databases.read_jobs(state_db) if scan_rep.has_state_db else []
    edges = databases.read_spawn_edges(state_db) if scan_rep.has_state_db else []
    enrollments = databases.read_enrollments(state_db) if scan_rep.has_state_db else []
    for row in enrollments:
        acct = row.get("account_id")
        if isinstance(acct, str) and acct.strip():
            secrets.append(acct.strip())
    dev_db = databases.read_dev_db(source / "sqlite" / "codex-dev.db") if scan_rep.has_dev_db else {}

    # Newer databases
    if scan_rep.has_goals_db:
        goals_1 = databases.read_goals(source / "goals_1.sqlite")
        if goals_1:
            goals = goals_1
    if scan_rep.has_queue_db:
        queue = databases.read_queue(source / "queue_1.sqlite")
    else:
        queue = []
    if scan_rep.has_memories_db:
        memories_rows = databases.read_memories(source / "memories_1.sqlite")
    else:
        memories_rows = []

    log(f"State DB: {len(threads)} threads, {len(dyn_tools)} dynamic tools, {len(goals)} goals, "
        f"{len(jobs)} jobs, {len(edges)} spawn edges")

    memories: list[str] = []
    mem_dir = source / "memories"
    if mem_dir.is_dir():
        memories = [p.relative_to(source).as_posix() for p in sorted(mem_dir.rglob("*.md"))[:50]]

    redactor = Redactor(
        level=options.redaction_level,
        home_dirs=[source.parent, Path.home()],
        secrets=secrets,
    )
    log(f"Redaction level: {options.redaction_level} ({len(secrets)} hard secrets registered)")

    # -- step 4: sessions ----------------------------------------------------- #
    entries = build_session_list(source, scan_rep)
    log(f"Recovering {len(entries)} sessions…")
    total_bytes = 0
    for path in scan_rep.transcript_files + scan_rep.archived_files:
        try:
            total_bytes += path.stat().st_size
        except OSError:
            pass

    parsed_bytes = 0
    sessions: list[SessionRecord] = []
    for i, entry in enumerate(entries, 1):
        _check(cancel)
        sid = entry["id"]
        path, rel = scanner.resolve_transcript(source, sid)
        share = 0.0
        if path is not None and total_bytes:
            try:
                share = path.stat().st_size / total_bytes
            except OSError:
                share = 0.0

        def sub(frac: float, _i: int = i, _n: int = max(len(entries), 1)) -> None:
            overall = (_i - 1 + min(max(frac, 0.0), 1.0)) / _n
            progress(0.15 + 0.70 * overall, f"Parsing session {_i}/{_n}…")

        sub(0.0)
        log(f"Parsing session {i}/{len(entries)}: {redactor.session_id(sid)}")
        rec = parse_transcript(path, session_id=sid,
                               on_progress=lambda f: sub(f), cancel=cancel) if path else \
            SessionRecord(session_id=sid, transcript_missing=True)
        if path is None:
            rec.transcript_missing = True
            rec.warnings.append("transcript missing (index reference unresolved)")
            log(f"  transcript missing for {redactor.session_id(sid)}", "warn")
        rec.transcript_rel = rel
        rec.archived = bool(entry.get("archived")) or (rel.startswith("archived_sessions") if rel else False)
        rec.index_row = entry
        row = thread_by_id.get(sid)
        rec.db_row = row
        if row:
            rec.db_tokens_used = int(row.get("tokens_used") or 0) or None
            if row.get("archived"):
                rec.archived = True
            rec.cwd = rec.cwd or str(row.get("cwd") or "")
            rec.created_at = rec.created_at or str(row.get("created_at") or "")
            rec.agent_nickname = rec.agent_nickname or str(row.get("agent_nickname") or "")
            rec.agent_role = rec.agent_role or str(row.get("agent_role") or "")
        rec.updated_at = (
            str(row.get("updated_at") or "") if row and row.get("updated_at")
            else entry.get("index_updated") or rec.created_at
        )
        title = entry.get("index_title") or (str(row.get("title") or "") if row else "")
        if not title:
            for turn in rec.turns:
                for msg in turn.messages():
                    if msg.role == "user":
                        title = " ".join(msg.text.split())[:64]
                        break
                if title:
                    break
        rec.title = title or "Untitled session"
        if not rec.model and row and row.get("model"):
            rec.model = str(row["model"])
        sessions.append(rec)
        parsed_bytes += rec.size_bytes
        for warning in rec.warnings:
            result.warnings.append(f"{redactor.session_id(sid)}: {warning}")

    sessions.sort(key=lambda s: s.updated_at or s.created_at or "")

    # -- step 4b: link subagents --------------------------------------------- #
    if options.include_subagents:
        _check(cancel)
        progress(0.86, "Linking subagents…")
        _link_subagents(sessions, scan_rep)
        subagent_count = sum(1 for s in sessions if s.is_subagent)
        parent_count = sum(1 for s in sessions if s.has_subagents)
        log(f"Linked {subagent_count} subagent sessions under {parent_count} parents")

    result.sessions = sessions

    # -- step 5: logs ---------------------------------------------------------- #
    logs: list[dict] = []
    if options.include_logs and scan_rep.has_logs_db:
        _check(cancel)
        progress(0.90, "Reading application logs…")
        logs = databases.read_logs(source / "logs_2.sqlite", limit=options.log_limit)
        log(f"Loaded {len(logs)} log entries")

    # -- step 6: statistics ----------------------------------------------------- #
    progress(0.94, "Computing statistics…")
    model_counter: Counter = Counter()
    for sess in sessions:
        if sess.model:
            model_counter[sess.model] += 1
    tokens_total = sum(s.db_tokens_used or 0 for s in sessions)
    if not tokens_total:
        tokens_total = sum((s.total_tokens.total_tokens if s.total_tokens else 0) for s in sessions)
    dates = [s.created_at or s.updated_at for s in sessions if (s.created_at or s.updated_at)]
    stats = {
        "total_sessions": len(sessions),
        "archived": sum(1 for s in sessions if s.archived),
        "active": sum(1 for s in sessions if not s.archived),
        "subagents": sum(1 for s in sessions if s.is_subagent),
        "parents": sum(1 for s in sessions if s.has_subagents),
        "total_tokens": tokens_total,
        "most_used_model": (f"{model_counter.most_common(1)[0][0]} ({model_counter.most_common(1)[0][1]} sessions)"
                            if model_counter else "-"),
        "date_from": min(dates)[:10] if dates else "-",
        "date_to": max(dates)[:10] if dates else "-",
        "messages": sum(sum(1 for k, _ in t.items if k == "msg") for s in sessions for t in s.turns),
        "tool_calls": sum(len(t.tool_calls()) for s in sessions for t in s.turns),
        "reasoning_blocks": sum(s.reasoning_blocks for s in sessions),
        "transcript_bytes": parsed_bytes,
        "corrupt_lines": sum(s.corrupted_lines for s in sessions),
        "missing_transcripts": sum(1 for s in sessions if s.transcript_missing),
    }
    result.stats = stats

    # -- step 7: render + write --------------------------------------------------- #
    _check(cancel)
    progress(0.96, "Writing combined report…")
    from .. import __version__

    bundle = {
        "source": source,
        "options": options,
        "redactor": redactor,
        "sessions": sessions,
        "scan": scan_rep,
        "generated_at": report.now_stamp(),
        "version": __version__,
        "warnings": result.warnings,
        "stats": stats,
        "logs": logs,
        "app_state": {
            "config": configs.config_summary(cfg),
            "auth": configs.auth_summary(auth),
            "global_state": configs.global_state_summary(gstate),
            "threads": threads,
            "goals": goals,
            "jobs": jobs,
            "edges": edges,
            "enrollments": enrollments,
            "dev_db": dev_db,
            "memories": memories,
            "queue": queue,
            "memories_rows": memories_rows,
        },
    }
    markdown = report.build_report(bundle)
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as fh:
        fh.write(markdown)
    log(f"Combined report: {output} ({len(markdown):,} chars)")

    # -- step 7b: per-session files ----------------------------------------------- #
    if options.per_sessions and sessions:
        _check(cancel)
        progress(0.97, "Writing per-session reports…")
        per_session_dir = output.parent / f"{output.stem}_sessions"
        per_session_dir.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        total = len(sessions)
        for idx, sess in enumerate(sessions, 1):
            _check(cancel)
            sess_md = report.build_session_report(sess, bundle)
            # Build a safe filename
            raw_title = sess.title or sess.session_id
            # Truncate absurdly long titles (e.g. guardian_review dumps the whole transcript)
            if len(raw_title) > 200:
                raw_title = raw_title[:200] + "…"
            safe_title = report._slug(raw_title) or f"session-{sess.session_id[:8]}"
            if not safe_title:
                safe_title = f"session-{sess.session_id[:8]}"
            # Prefix with subagent indicator
            if sess.is_subagent:
                safe_title = f"sub_{safe_title}"
            # Hard cap filename length (Windows MAX_PATH = 260, leave room for dir)
            if len(safe_title) > 120:
                safe_title = safe_title[:120].rstrip("-_")
            fname = f"{idx:02d}_{safe_title}.md"
            fpath = per_session_dir / fname
            try:
                fpath.write_text(sess_md, encoding="utf-8")
            except OSError:
                # Fallback: use only session ID for filename if path still too long
                fname = f"{idx:02d}_session_{sess.session_id[:8]}.md"
                fpath = per_session_dir / fname
                fpath.write_text(sess_md, encoding="utf-8")
            written.append(fpath)
            log(f"  Session {idx}/{total}: {fname} ({len(sess_md):,} chars)")
            progress(0.97 + 0.03 * (idx / total), f"Writing per-session reports ({idx}/{total})…")
        result.per_session_paths = written
        log(f"Per-session reports: {len(written)} files in {per_session_dir}")

    progress(1.0, "Done")
    return result
