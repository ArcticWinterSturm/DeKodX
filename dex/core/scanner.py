"""Directory scanning: locate and inventory a ``.codex`` installation."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .models import ScanReport, VersionInfo

EXPECTED_FILES = (
    "session_index.jsonl",
    "state_5.sqlite",
    "logs_2.sqlite",
    "config.toml",
    "auth.json",
    ".codex-global-state.json",
    "installation_id",
    "cap_sid",
)

# Newer additions
OPTIONAL_FILES = (
    "thread_history_1.sqlite",
    "goals_1.sqlite",
    "queue_1.sqlite",
    "memories_1.sqlite",
    "models_cache.json",
    "external_agent_session_imports.json",
)


def detect_default_source() -> Path:
    """Best guess for the Codex data directory on this machine."""
    candidate = Path.home() / ".codex"
    if candidate.is_dir():
        return candidate
    for env in ("CODEX_HOME", "CODEX_DATA_DIR"):
        val = os.environ.get(env)
        if val and Path(val).is_dir():
            return Path(val)
    return candidate


def read_jsonl_lines(path: Path, limit: int | None = None) -> list[dict]:
    """Parse a JSONL file, silently skipping blank/corrupt lines."""
    out: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
                if limit and len(out) >= limit:
                    break
    except OSError:
        pass
    return out


def enumerate_transcripts(source: Path) -> tuple[list[Path], list[Path]]:
    """Find all transcript JSONL files (active + archived)."""
    active: list[Path] = []
    sessions_dir = source / "sessions"
    if sessions_dir.is_dir():
        active = sorted(p for p in sessions_dir.rglob("*.jsonl") if p.is_file())
    archived: list[Path] = []
    arch_dir = source / "archived_sessions"
    if arch_dir.is_dir():
        archived = sorted(p for p in arch_dir.rglob("*.jsonl") if p.is_file())
    return active, archived


def _detect_schema_versions(transcript_files: list[Path]) -> list[str]:
    """Sample a few transcript files to detect schema versions."""
    versions: set[str] = set()
    for path in transcript_files[:5]:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f):
                    if i > 20:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except:
                        continue
                    # v2 has agent_message, inter_agent_communication_metadata
                    if obj.get("type") == "response_item":
                        payload = obj.get("payload", {})
                        if payload.get("type") == "agent_message":
                            versions.add("v2 (agent_message)")
                        if payload.get("type") == "custom_tool_call":
                            versions.add("v2 (custom_tool_call)")
                    if obj.get("type") == "inter_agent_communication_metadata":
                        versions.add("v2 (inter_agent)")
        except OSError:
            pass
    return sorted(versions)


def _detect_config_format(source: Path) -> tuple[str, VersionInfo]:
    """Read config to determine version + format."""
    vi = VersionInfo()
    config_path = source / "config.toml"
    if config_path.exists():
        try:
            # Quick parse for version indicators
            text = config_path.read_text(encoding="utf-8", errors="replace")
            # Check for new fields
            if "chatgpt-conversation-resume-tokens" in text:
                vi.config_format = "v2 (chatgpt-integrated)"
            elif "external-agent-import-sync" in text:
                vi.config_format = "v2 (external-agent-sync)"
            else:
                vi.config_format = "v1 (classic codex)"
            # Extract model
            m = re.search(r'model\s*=\s*"([^"]+)"', text)
            if m:
                vi.cli_version = m.group(1)
        except OSError:
            pass

    # Check global state for desktop app version
    gstate_path = source / ".codex-global-state.json"
    if gstate_path.exists():
        try:
            text = gstate_path.read_text(encoding="utf-8", errors="replace")
            m = re.search(r'"electron-app-version"\s*:\s*"?([^",}\s]+)"?', text)
            if m:
                vi.desktop_app_version = m.group(1)
            # Also look for codex-app-version in plugins
            m = re.search(r'"codex-app-version"\s*:\s*"?([^",}\s]+)"?', text)
            if m and not vi.desktop_app_version:
                vi.desktop_app_version = m.group(1)
        except OSError:
            pass

    return vi.config_format, vi


def _detect_db_versions(source: Path, vi: VersionInfo) -> None:
    """Detect SQLite schema versions."""
    import sqlite3

    def get_user_version(path: Path) -> str:
        try:
            conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5)
            row = conn.execute("PRAGMA user_version").fetchone()
            conn.close()
            return str(row[0]) if row else "0"
        except:
            return "?"

    state_path = source / "state_5.sqlite"
    if state_path.exists():
        vi.state_db_version = get_user_version(state_path)

    logs_path = source / "logs_2.sqlite"
    if logs_path.exists():
        vi.logs_db_version = get_user_version(logs_path)


def _find_subagent_candidates(source: Path) -> list[dict]:
    """Find sessions that look like subagents (guardian_review, subagent source)."""
    candidates = []
    active, archived = enumerate_transcripts(source)
    for path in active + archived:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f):
                    if i > 10:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except:
                        continue
                    if obj.get("type") == "session_meta":
                        payload = obj.get("payload", {})
                        source_info = payload.get("source", {})
                        # source can be a string ("vscode", "cli") or a dict
                        if isinstance(source_info, str):
                            source_info = {}
                        thread_source = payload.get("thread_source", "")
                        parent_id = payload.get("parent_thread_id", "")
                        if thread_source == "guardian_review" or source_info.get("subagent"):
                            candidates.append({
                                "path": path,
                                "session_id": payload.get("id", ""),
                                "parent_thread_id": parent_id,
                                "thread_source": thread_source,
                                "source": source_info,
                                "cli_version": payload.get("cli_version", ""),
                            })
                        break
        except OSError:
            pass
    return candidates


def scan(source: Path) -> ScanReport:
    """Inventory the directory without doing any heavy parsing."""
    rep = ScanReport(source=Path(source))
    if not rep.source.is_dir():
        rep.issues.append(f"Source directory does not exist: {source}")
        return rep
    rep.exists = True

    rep.present_files = {name: (rep.source / name).exists() for name in EXPECTED_FILES}
    for name in OPTIONAL_FILES:
        rep.present_files[name] = (rep.source / name).exists()

    rep.looks_like_codex = any(
        (rep.source / name).exists()
        for name in ("session_index.jsonl", "state_5.sqlite", "sessions", "config.toml")
    )
    if not rep.looks_like_codex:
        rep.issues.append("Directory does not look like a Codex data directory")

    rep.index_entries = read_jsonl_lines(rep.source / "session_index.jsonl")
    rep.transcript_files, rep.archived_files = enumerate_transcripts(source)
    rep.has_state_db = (rep.source / "state_5.sqlite").is_file()
    rep.has_logs_db = (rep.source / "logs_2.sqlite").is_file()
    rep.has_dev_db = (rep.source / "sqlite" / "codex-dev.db").is_file()
    rep.has_thread_history_db = (rep.source / "thread_history_1.sqlite").is_file()
    rep.has_goals_db = (rep.source / "goals_1.sqlite").is_file()
    rep.has_queue_db = (rep.source / "queue_1.sqlite").is_file()
    rep.has_memories_db = (rep.source / "memories_1.sqlite").is_file()

    # Version detection
    _, vi = _detect_config_format(source)
    _detect_db_versions(source, vi)
    rep.version = vi

    # Detect schema versions from transcripts
    all_transcripts = rep.transcript_files + rep.archived_files
    rep.version.rollout_schema_versions = _detect_schema_versions(all_transcripts)

    # Find subagent candidates
    rep.subagent_candidates = _find_subagent_candidates(source)

    return rep


def resolve_transcript(source: Path, session_id: str) -> tuple[Path | None, str]:
    """Locate the transcript file for a session id.

    Returns (path_or_None, relative posix path or '').
    """
    sessions_dir = source / "sessions"
    if sessions_dir.is_dir():
        hits = sorted(sessions_dir.rglob(f"rollout-*-{session_id}.jsonl"))
        if not hits:  # tolerate non-rollout naming
            hits = sorted(sessions_dir.rglob(f"*{session_id}.jsonl"))
        if hits:
            return hits[0], hits[0].relative_to(source).as_posix()
    arch_dir = source / "archived_sessions"
    if arch_dir.is_dir():
        hits = sorted(arch_dir.rglob(f"rollout-*-{session_id}.jsonl"))
        if not hits:
            hits = sorted(arch_dir.rglob(f"*{session_id}.jsonl"))
        if hits:
            return hits[0], hits[0].relative_to(source).as_posix()
    return None, ""


def quick_stats(source: Path) -> dict:
    """Cheap headline numbers for the dashboard (no transcript parsing)."""
    rep = scan(source)
    stats: dict = {
        "exists": rep.exists,
        "looks_like_codex": rep.looks_like_codex,
        "present_files": rep.present_files,
        "issues": rep.issues,
        "index_sessions": len(rep.index_entries),
        "active_transcripts": len(rep.transcript_files),
        "archived_transcripts": len(rep.archived_files),
        "threads": 0,
        "tokens_used": 0,
        "date_range": ("", ""),
        "has_state_db": rep.has_state_db,
        "has_logs_db": rep.has_logs_db,
        "has_dev_db": rep.has_dev_db,
        "has_thread_history_db": rep.has_thread_history_db,
        "has_goals_db": rep.has_goals_db,
        "has_queue_db": rep.has_queue_db,
        "has_memories_db": rep.has_memories_db,
        "log_rows": 0,
        "size_mb": 0.0,
        "subagent_candidates": len(rep.subagent_candidates),
        "config_format": rep.version.config_format,
        "cli_version": rep.version.cli_version,
        "app_version": rep.version.desktop_app_version,
        "state_db_version": rep.version.state_db_version,
        "logs_db_version": rep.version.logs_db_version,
    }
    if rep.has_state_db:
        from .databases import read_threads, count_rows

        threads = read_threads(source / "state_5.sqlite")
        stats["threads"] = len(threads)
        stats["tokens_used"] = sum(int(t.get("tokens_used") or 0) for t in threads)
        stamps = [str(t.get("updated_at") or "") for t in threads if t.get("updated_at")]
        if stamps:
            stats["date_range"] = (min(stamps)[:10], max(stamps)[:10])
        stats["log_rows"] = count_rows(source / "logs_2.sqlite", "logs") if rep.has_logs_db else 0

    # One walk, not four: the old code ran rglob() once per extension,
    # re-traversing the entire (often multi-GB) .codex tree four times on
    # every dashboard scan — a disk I/O storm on Windows at app startup.
    total = 0
    wanted_suffixes = (".jsonl", ".sqlite", ".json", ".toml")
    for dirpath, _dirnames, filenames in os.walk(rep.source):
        for name in filenames:
            if name.endswith(wanted_suffixes):
                try:
                    total += os.path.getsize(os.path.join(dirpath, name))
                except OSError:
                    pass
    stats["size_mb"] = round(total / (1024 * 1024), 1)
    return stats
