"""Headless CLI for DeKodX — recover, audit, capture and chatwork subcommands.

recover   — full Markdown report generation (default)
audit     — per-thread, per-response, per-turn audit using v2 infrastructure
capture   — live analytics capture daemon
chatwork  — chatgpt.com (non-Codex) Chat & Work local archaeology

Usage:
  python run.py cli --help
  python run.py cli recover --source ~/.codex --output report.md
  python run.py cli audit --logs --grep rate_limit
  python run.py cli capture --interval 2.0
  python run.py cli chatwork --exports ~/Desktop/navier-stokes-audit.zip
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

from .core import scanner
from .core.models import RecoveryCancelled, RecoveryOptions
from .core.recovery import run_recovery
from .core.audit import run_audit
from .core.capture import run_capture_daemon


def _bar(frac: float, width: int = 36) -> str:
    filled = int(round(frac * width))
    return "█" * filled + "░" * (width - filled)


def _cmd_recover(args: argparse.Namespace) -> int:
    """Run the recovery subcommand."""
    source = args.source or scanner.detect_default_source()
    output = args.output or Path.cwd() / "DeKodX_recovery.md"

    def log(msg: str, level: str = "info") -> None:
        if args.quiet and level == "info":
            return
        stamp = time.strftime("%H:%M:%S")
        prefix = {"warn": "!", "error": "x"}.get(level, "*")
        print(f"[{stamp}] {prefix} {msg}")

    if args.scan_only:
        stats = scanner.quick_stats(source)
        print(f"Source            : {source}")
        print(f"Looks like Codex  : {stats['looks_like_codex']}")
        print(f"Index sessions    : {stats['index_sessions']}")
        print(f"Active transcripts: {stats['active_transcripts']}")
        print(f"Archived          : {stats['archived_transcripts']}")
        print(f"Subagent candidates: {stats.get('subagent_candidates', 0)}")
        print(f"Threads in DB     : {stats['threads']}")
        print(f"Tokens used       : {stats['tokens_used']:,}")
        print(f"Log rows          : {stats['log_rows']:,}")
        print(f"Data size         : {stats['size_mb']} MB")
        if stats.get('app_version'):
            print(f"App version       : {stats['app_version']}")
        if stats.get('cli_version'):
            print(f"CLI version       : {stats['cli_version']}")
        if stats.get('config_format'):
            print(f"Config format     : {stats['config_format']}")
        return 0

    options = RecoveryOptions(
        include_transcripts=not args.no_transcripts,
        include_app_state=not args.no_app_state,
        include_tokens=not args.no_tokens,
        include_tools=not args.no_tools,
        include_logs=not args.no_logs,
        log_limit=args.log_limit,
        redaction_level=args.level,
        per_sessions=not args.no_per_sessions,
        include_subagents=not args.no_subagents,
        flatten_subagents=args.flatten_subagents,
    )

    last_pct = [-1]

    def progress(frac: float, label: str = "") -> None:
        pct = int(frac * 100)
        if pct == last_pct[0]:
            return
        last_pct[0] = pct
        width = shutil.get_terminal_size((80, 24)).columns
        line = f"\r[{_bar(frac, max(width - 30, 10))}] {pct:3d}%  {label[:24]:24s}"
        sys.stdout.write(line)
        sys.stdout.flush()

    try:
        result = run_recovery(source, output, options, log=log, progress=progress)
    except RecoveryCancelled:
        print("\ncancelled")
        return 130
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        print()

    stats = result.stats
    log(f"Sessions recovered : {stats['total_sessions']} ({stats['archived']} archived)")
    log(f"Subagents          : {stats.get('subagents', 0)} under {stats.get('parents', 0)} parents")
    log(f"Tokens             : {stats['total_tokens']:,}")
    log(f"Report             : {result.output_path}")
    if result.per_session_paths:
        log(f"Per-session files  : {len(result.per_session_paths)} in {output.stem}_sessions/")
    return 0


def _cmd_audit(args: argparse.Namespace) -> int:
    """Run the audit subcommand."""
    source = args.source or scanner.detect_default_source()
    output = run_audit(
        source,
        logs=args.logs,
        grep=args.grep,
        since=args.since,
        quiet_state=args.quiet_state,
        output_json=args.json,
    )
    print(output)
    return 0


def _cmd_capture(args: argparse.Namespace) -> int:
    """Run the capture daemon subcommand."""
    source = args.source or scanner.detect_default_source()
    run_capture_daemon(source, interval=args.interval)
    return 0


def _cmd_chatwork(args: argparse.Namespace) -> int:
    """Run the Chat & Work archaeology subcommand."""
    from .core.chatwork import run_chatwork
    from .core.chatwork_report import build_chatwork_report
    from .core.redaction import Redactor
    from dex import __version__

    codex_home = args.source or scanner.detect_default_source()
    output = args.output or Path.cwd() / "DeKodX_chatwork.md"
    exports = [Path(p) for p in (args.exports or [])]

    def log(msg: str, level: str = "info") -> None:
        if args.quiet and level == "info":
            return
        stamp = time.strftime("%H:%M:%S")
        prefix = {"warn": "!", "error": "x"}.get(level, "*")
        print(f"[{stamp}] {prefix} {msg}")

    last_pct = [-1]

    def progress(frac: float, label: str = "") -> None:
        pct = int(frac * 100)
        if pct == last_pct[0]:
            return
        last_pct[0] = pct
        width = shutil.get_terminal_size((80, 24)).columns
        sys.stdout.write(f"\r[{_bar(frac, max(width - 30, 10))}] {pct:3d}%  {label[:24]:24s}")
        sys.stdout.flush()

    try:
        report = run_chatwork(
            codex_home, exports,
            log=log, progress=progress,
            scan_browser=not args.no_browser,
            scan_rollout_bodies=not args.no_rollouts,
        )
    finally:
        print()

    redactor = Redactor(level=args.level, home_dirs=[Path.home()])
    markdown = build_chatwork_report(report, redactor, version=__version__)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(markdown, encoding="utf-8")

    stats = report.stats()
    log(f"Catalog threads : {stats['catalog_threads']} ({stats['tpp_threads']} tpp)")
    log(f"Rollouts linked : {stats['rollout_linked']}"
        + (f" (+{stats['orphan_rollouts']} orphans)" if stats['orphan_rollouts'] else ""))
    log(f"Browser profiles: {stats['browsers']} ({stats['browser_visits']} history rows)")
    log(f"Exports triaged : {stats['exports']}")
    log(f"Report          : {output}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dekodx",
        description="DeKodX - forensic recovery and analytics for Codex Desktop conversation data.",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # --- recover ---
    rec = subparsers.add_parser("recover", help="Generate Markdown recovery report")
    rec.add_argument("--source", "-s", type=Path, default=None,
                     help="path to .codex directory (default: auto-detect)")
    rec.add_argument("--output", "-o", type=Path, default=None,
                     help="output .md path (default: ./DeKodX_recovery.md)")
    rec.add_argument("--level", "-l", choices=RecoveryOptions.LEVELS, default="standard",
                     help="redaction level (default: standard)")
    rec.add_argument("--no-transcripts", action="store_true", help="exclude conversation transcripts")
    rec.add_argument("--no-app-state", action="store_true", help="exclude application state section")
    rec.add_argument("--no-tokens", action="store_true", help="exclude token usage tables")
    rec.add_argument("--no-tools", action="store_true", help="exclude tool call details")
    rec.add_argument("--no-logs", action="store_true", help="exclude application logs")
    rec.add_argument("--no-per-sessions", action="store_true",
                     help="do not write individual per-session .md files")
    rec.add_argument("--no-subagents", action="store_true",
                     help="do not include subagent sessions in reports")
    rec.add_argument("--flatten-subagents", action="store_true",
                     help="show subagent turns inline in parent session")
    rec.add_argument("--log-limit", type=int, default=1000, help="how many log rows to include")
    rec.add_argument("--scan-only", action="store_true", help="only inventory, no report")
    rec.add_argument("--quiet", "-q", action="store_true", help="only warnings and errors")
    rec.set_defaults(func=_cmd_recover)

    # --- audit ---
    aud = subparsers.add_parser("audit", help="Per-thread, per-response, per-turn audit")
    aud.add_argument("--source", "-s", type=Path, default=None,
                     help="path to .codex directory (default: auto-detect)")
    aud.add_argument("--logs", action="store_true", help="query logs_2 tracing DB")
    aud.add_argument("--grep", default=None, help="substring filter for --logs rows")
    aud.add_argument("--since", default=None, help="ISO date for --logs")
    aud.add_argument("--quiet-state", action="store_true", help="skip per-DB log-target breakdown")
    aud.add_argument("--json", action="store_true", help="output JSON instead of text")
    aud.set_defaults(func=_cmd_audit)

    # --- capture ---
    cap = subparsers.add_parser("capture", help="Live analytics capture daemon")
    cap.add_argument("--source", "-s", type=Path, default=None,
                     help="path to .codex directory (default: auto-detect)")
    cap.add_argument("--interval", "-i", type=float, default=2.0,
                     help="polling interval in seconds (default: 2.0)")
    cap.set_defaults(func=_cmd_capture)

    # --- chatwork ---
    cw = subparsers.add_parser(
        "chatwork", help="ChatGPT (non-Codex) Chat & Work local archaeology")
    cw.add_argument("--source", "-s", type=Path, default=None,
                    help="path to .codex directory holding sqlite/codex-dev.db "
                         "(default: auto-detect)")
    cw.add_argument("--output", "-o", type=Path, default=None,
                    help="output .md path (default: ./DeKodX_chatwork.md)")
    cw.add_argument("--exports", "-e", type=Path, nargs="*", default=None,
                    help="export archives to triage (.zip/.json/.md)")
    cw.add_argument("--level", "-l", choices=RecoveryOptions.LEVELS, default="standard",
                    help="redaction level (default: standard)")
    cw.add_argument("--no-browser", action="store_true",
                    help="skip Chromium browser trace scan")
    cw.add_argument("--no-rollouts", action="store_true",
                    help="skip scanning codex rollouts for chatgpt links")
    cw.add_argument("--quiet", "-q", action="store_true", help="only warnings and errors")
    cw.set_defaults(func=_cmd_chatwork)

    # Default to recover if no subcommand given (backward compat)
    if argv and argv[0] not in ("recover", "audit", "capture", "chatwork", "-h", "--help"):
        argv.insert(0, "recover")

    args = parser.parse_args(argv)
    if hasattr(args, "func"):
        return args.func(args)
    return _cmd_recover(args)


if __name__ == "__main__":
    raise SystemExit(main())
