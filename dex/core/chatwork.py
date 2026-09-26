"""Chat & Work archaeology for chatgpt.com (non-Codex) local storage.

Recovers what a ChatGPT *web/desktop* account leaves on this machine:

  1. Thread catalog  - ~/.codex/sqlite/codex-dev.db `local_thread_catalog`
     (titles, thread ids, timestamps, source_kind, conversation_origin tpp)
     synced from the Codex Desktop app's ChatGPT host bridge.
  2. Codex sessions  - rollout JSONLs whose session_meta carries a
     `chatgpt_conversation_id` / `chatgpt_account_id` (agent chats surfaced
     in chatgpt.com), linked back to catalog rows.
  3. Browser traces  - Chromium-family profile scan:
       History sqlite      -> chatgpt.com/c/<thread-id> visits
       IndexedDB leveldb   -> https_chatgpt.com_0 (compressed, flagged only)
       Service Worker cache-> chatgpt.com origins
       Local Storage       -> key names only (values are leveldb/compressed)
  4. Export archives  - user-supplied .zip / .json / .md exports
     (Settings > Data controls > Export data, shared-chat archives,
     sandbox download bundles like navier-stokes-audit.zip) with per-file
     PII triage (emails / keys / SIDs / JWTs found inside).

Everything is opened READ-ONLY. No network access, ever.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

LogFn = Callable[[str, str], None]
ProgressFn = Callable[[float, str], None]

# --------------------------------------------------------------------------- #
# models
# --------------------------------------------------------------------------- #

THREAD_URL = "https://chatgpt.com/c/{thread_id}"

PII_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("api-key", re.compile(r"\b(?:sk|pk|rk)-(?:proj-)?[A-Za-z0-9_-]{10,}\b")),
    ("windows-sid", re.compile(r"\bS-1-\d+(?:-\d+)+\b")),
    ("ipv4", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("home-path", re.compile(r"(?:[A-Za-z]:\\Users\\|/home/)[^\s\"'<>|]+")),
)


@dataclass
class ChatThread:
    """One chatgpt.com conversation known locally."""
    thread_id: str
    title: str = ""
    source_kind: str = ""          # chatgpt | vscode
    conversation_origin: str = ""  # tpp | "" (tpp = typed-in-ChatGPT-product)
    created_at: str = ""           # ISO from epoch float
    updated_at: str = ""
    host_id: str = ""
    cwd: str = ""
    model: str = ""
    originators: list[str] = field(default_factory=list)
    local_rollout: str = ""        # relative path of linked codex rollout
    rollout_turns: int = 0
    rollout_messages: int = 0
    rollout_tools: int = 0
    browser_visits: int = 0
    last_browser_visit: str = ""
    catalog_only: bool = True       # False once a local rollout is linked

    @property
    def url(self) -> str:
        return THREAD_URL.format(thread_id=self.thread_id)


@dataclass
class BrowserTrace:
    browser: str                   # Chrome / Edge / Brave / Vivaldi / Opera
    profile: str                   # Default, Profile 1, ...
    history_hits: int = 0
    last_visit: str = ""
    indexeddb_bytes: int = 0
    service_worker_caches: int = 0
    local_storage_keys: int = 0
    notes: list[str] = field(default_factory=list)


@dataclass
class ExportArtifact:
    path: Path
    kind: str = ""                 # zip | json | markdown | unknown
    size_bytes: int = 0
    sha256: str = ""
    entries: list[str] = field(default_factory=list)
    pii_hits: dict[str, int] = field(default_factory=dict)
    chat_thread_refs: list[str] = field(default_factory=list)
    sandbox_ids: list[str] = field(default_factory=list)
    error: str = ""


@dataclass
class ChatWorkReport:
    source_codex: Path
    catalog_threads: list[ChatThread] = field(default_factory=list)
    rollout_threads: list[ChatThread] = field(default_factory=list)   # chatgpt-linked rollouts not in catalog
    browsers: list[BrowserTrace] = field(default_factory=list)
    exports: list[ExportArtifact] = field(default_factory=list)
    hosts: list[dict] = field(default_factory=list)
    sync_state: list[dict] = field(default_factory=list)
    global_state: Optional["ChatGlobalState"] = None
    warnings: list[str] = field(default_factory=list)

    # -- aggregates -------------------------------------------------------- #
    @property
    def all_threads(self) -> list[ChatThread]:
        seen: set[str] = set()
        out: list[ChatThread] = []
        for t in sorted(self.catalog_threads + self.rollout_threads,
                        key=lambda t: t.updated_at or t.created_at, reverse=True):
            if t.thread_id in seen:
                continue
            seen.add(t.thread_id)
            out.append(t)
        return out

    @property
    def tpp_thread_count(self) -> int:
        return sum(1 for t in self.catalog_threads if t.conversation_origin == "tpp")

    @property
    def linked_rollout_count(self) -> int:
        return sum(1 for t in self.all_threads if not t.catalog_only)

    def stats(self) -> dict:
        visits = sum(b.history_hits for b in self.browsers)
        idb = sum(b.indexeddb_bytes for b in self.browsers)
        return {
            "catalog_threads": len(self.catalog_threads),
            "tpp_threads": self.tpp_thread_count,
            "rollout_linked": self.linked_rollout_count,
            "orphan_rollouts": len(self.rollout_threads),
            "browsers": sum(1 for b in self.browsers if b.history_hits or b.indexeddb_bytes),
            "browser_visits": visits,
            "indexeddb_kb": idb // 1024,
            "exports": len(self.exports),
            "export_pii_files": sum(1 for e in self.exports if e.pii_hits),
        }


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _epoch_to_iso(value) -> str:
    try:
        if value is None or value == "":
            return ""
        f = float(value)
        if f > 1e12:            # already milliseconds
            f /= 1000.0
        return datetime.fromtimestamp(f, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, OSError, OverflowError):
        return str(value)[:19]


def _connect_ro(path: Path) -> Optional[sqlite3.Connection]:
    try:
        conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        return None


def _chromium_browsers() -> list[tuple[str, Path]]:
    """(browser name, User Data dir) for installed Chromium browsers."""
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    roams = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    cands = [
        ("Chrome", local / "Google" / "Chrome" / "User Data"),
        ("Edge", local / "Microsoft" / "Edge" / "User Data"),
        ("Brave", local / "BraveSoftware" / "Brave-Browser" / "User Data"),
        ("Vivaldi", local / "Vivaldi" / "User Data"),
        ("Opera", roams / "Opera Software" / "Opera Stable"),
        ("OperaGX", roams / "Opera Software" / "OperaGX Stable"),
    ]
    return [(n, p) for n, p in cands if p.is_dir()]


def _profiles(user_data: Path) -> list[Path]:
    out = []
    if (user_data / "Local State").exists():
        for child in sorted(user_data.iterdir()):
            if child.is_dir() and (
                child.name == "Default" or re.fullmatch(r"Profile \d+", child.name)
            ):
                out.append(child)
    else:                        # Opera-style: the dir itself is the profile
        out.append(user_data)
    return out


def _copy_temp(src: Path, tmp_dir: Path) -> Optional[Path]:
    """Copy a locked sqlite to temp so we can open it (Chrome holds locks)."""
    import shutil
    try:
        dst = tmp_dir / f"{src.parent.name}_{src.name}"
        shutil.copy2(src, dst)
        return dst
    except OSError:
        return None


# --------------------------------------------------------------------------- #
# 1. codex-dev.db thread catalog
# --------------------------------------------------------------------------- #

def read_catalog(codex_home: Path) -> tuple[list[ChatThread], list[dict], list[dict], list[str]]:
    """Read local_thread_catalog + hosts + sync state from codex-dev.db."""
    threads: list[ChatThread] = []
    hosts: list[dict] = []
    sync: list[dict] = []
    warnings: list[str] = []
    db = codex_home / "sqlite" / "codex-dev.db"
    if not db.is_file():
        warnings.append(f"catalog database not found: {db}")
        return threads, hosts, sync, warnings

    conn = _connect_ro(db)
    if conn is None:
        warnings.append("could not open codex-dev.db read-only")
        return threads, hosts, sync, warnings
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "local_thread_catalog" not in tables:
            warnings.append("local_thread_catalog table missing (older Codex?)")
            return threads, hosts, sync, warnings

        for row in conn.execute("SELECT * FROM local_thread_catalog"):
            d = dict(row)
            t = ChatThread(
                thread_id=str(d.get("thread_id") or ""),
                title=str(d.get("display_title") or ""),
                source_kind=str(d.get("source_kind") or ""),
                conversation_origin=str(d.get("conversation_origin") or ""),
                created_at=_epoch_to_iso(d.get("source_created_at")),
                updated_at=_epoch_to_iso(d.get("source_updated_at")),
                host_id=str(d.get("host_id") or ""),
                cwd=str(d.get("cwd") or ""),
                model=str(d.get("model_provider") or ""),
            )
            if t.thread_id:
                threads.append(t)
        if "local_thread_catalog_hosts" in tables:
            hosts = [dict(r) for r in conn.execute(
                "SELECT * FROM local_thread_catalog_hosts")]
        if "local_thread_catalog_sync_state" in tables:
            sync = [dict(r) for r in conn.execute(
                "SELECT * FROM local_thread_catalog_sync_state")]
    except sqlite3.Error as exc:
        warnings.append(f"catalog read error: {exc}")
    finally:
        conn.close()
    return threads, hosts, sync, warnings


# --------------------------------------------------------------------------- #
# 1b. .codex-global-state.json chatgpt atoms
# --------------------------------------------------------------------------- #

@dataclass
class ChatGlobalState:
    """ChatGPT-related atoms from .codex-global-state.json."""
    thread_bindings: dict[str, str] = field(default_factory=dict)   # local id -> chatgpt thread id
    projects: list[dict] = field(default_factory=list)              # g-p-* projects
    resume_token_threads: list[str] = field(default_factory=list)   # thread ids with resume tokens
    last_model: str = ""


def read_global_state(codex_home: Path) -> tuple[ChatGlobalState, list[str]]:
    """Parse chatgpt-* atoms from .codex-global-state.json."""
    out = ChatGlobalState()
    warnings: list[str] = []
    path = codex_home / ".codex-global-state.json"
    if not path.is_file():
        warnings.append(f"global state not found: {path}")
        return out, warnings
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append(f"global state unreadable: {exc}")
        return out, warnings

    atom = data.get("electron-persisted-atom-state") or {}
    for key, val in atom.items():
        if key.startswith("client-thread-bindings-v1."):
            local_id = key.split(".", 1)[1]
            if isinstance(val, str) and val:
                out.thread_bindings[local_id] = val
        elif key == "chatgpt-sidebar-state-v1" or key.startswith("chatgpt-sidebar-state-v1."):
            # bare key: {host: {pinnedConversations, pinnedProjects, projects}}
            # dotted key: the value is one host's state
            host_states: list = []
            if key == "chatgpt-sidebar-state-v1" and isinstance(val, dict):
                host_states = list(val.values())
            elif isinstance(val, dict):
                host_states = [val]
            for host_state in host_states:
                if isinstance(host_state, dict):
                    for proj in host_state.get("projects") or []:
                        if isinstance(proj, dict):
                            out.projects.append({
                                "id": str(proj.get("id") or ""),
                                "name": str(proj.get("name") or ""),
                                "created": str(proj.get("createdAt") or "")[:19],
                                "updated": str(proj.get("updatedAt") or "")[:19],
                            })
    rt = atom.get("chatgpt-conversation-resume-tokens-v1") or {}
    if isinstance(rt, dict):
        for key in rt:
            # keys are JSON-encoded [user, host, thread_id] triples
            try:
                parts = json.loads(key)
                tid = parts[-1] if isinstance(parts, list) else str(parts)
            except (json.JSONDecodeError, TypeError):
                tid = str(key)
            if tid not in out.resume_token_threads:
                out.resume_token_threads.append(tid)
    lm = atom.get("chatgpt-last-selected-model-v1") or {}
    if isinstance(lm, dict):
        out.last_model = str(lm.get("slug") or "")
    return out, warnings


# --------------------------------------------------------------------------- #
# 2. chatgpt-linked codex rollouts
# --------------------------------------------------------------------------- #

_CHATGPT_KEYS = ("chatgpt_conversation_id", "chatgpt-conversation-id",
                 "chatgpt_account_id", "chatgpt-account-id", "conversation_id")
_ROLLOUT_ID = re.compile(r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                         r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})")


def scan_rollouts(codex_home: Path, catalog: list[ChatThread],
                  log: LogFn, progress: ProgressFn,
                  cancel: Optional[Callable[[], bool]] = None,
                  max_meta_lines: int = 40) -> tuple[list[ChatThread], list[str]]:
    """Find rollout JSONLs whose session_meta links them to a chatgpt.com thread.

    Returns (orphan ChatThreads not already in catalog, warnings).
    """
    warnings: list[str] = []
    by_thread: dict[str, ChatThread] = {t.thread_id: t for t in catalog}
    orphans: dict[str, ChatThread] = {}

    files: list[Path] = []
    for sub in ("sessions", "archived_sessions"):
        d = codex_home / sub
        if d.is_dir():
            files += sorted(d.rglob("*.jsonl"))
    total = max(len(files), 1)

    for i, path in enumerate(files, 1):
        if cancel is not None and cancel():
            warnings.append("rollout scan cancelled")
            break
        if i % 25 == 0 or i == total:
            progress(0.05 + 0.35 * (i / total), f"Scanning rollouts {i}/{total}")
        meta: dict = {}
        turns = msgs = tools = 0
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for lineno, line in enumerate(fh, 1):
                    if lineno > max_meta_lines and meta:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    p = obj.get("payload") or {}
                    if obj.get("type") == "session_meta" and not meta:
                        meta = p if isinstance(p, dict) else {}
                    elif obj.get("type") == "turn_context":
                        turns += 1
                    elif obj.get("type") == "event_msg":
                        et = p.get("type")
                        if et in ("user_message", "agent_message"):
                            msgs += 1
                        elif et and et.endswith(("_end", "_request", "_response")):
                            tools += 1
                    elif obj.get("type") == "response_item":
                        rt = p.get("type")
                        if rt == "message":
                            msgs += 1
                        elif rt and "tool_call" in rt:
                            tools += 1
                    if lineno > 4000:
                        break
        except OSError as exc:
            warnings.append(f"unreadable rollout {path.name}: {exc}")
            continue

        if not meta:
            continue
        conv_id = ""
        for key in _CHATGPT_KEYS:
            val = meta.get(key)
            if isinstance(val, str) and val:
                conv_id = val
                break
        # source == chatgpt also marks web-originated agent threads
        src = meta.get("source")
        src_str = src if isinstance(src, str) else ""
        if not conv_id and src_str != "chatgpt":
            continue
        if not conv_id:
            m = _ROLLOUT_ID.search(path.stem)
            conv_id = m.group(1) if m else path.stem

        rel = path.relative_to(codex_home).as_posix()
        target = by_thread.get(conv_id)
        if target is not None:
            target.catalog_only = False
            target.local_rollout = rel
            target.rollout_turns = turns
            target.rollout_messages = msgs
            target.rollout_tools = tools
            if not target.cwd and meta.get("cwd"):
                target.cwd = str(meta.get("cwd"))
            if meta.get("originator") and meta["originator"] not in target.originators:
                target.originators.append(str(meta["originator"]))
            log(f"linked rollout -> {conv_id[:8]} ({path.name[:40]}…)", "ok")
        elif conv_id not in orphans:
            t = ChatThread(
                thread_id=conv_id,
                title=str(meta.get("cwd") or path.stem)[:60],
                source_kind="chatgpt",
                created_at=str(meta.get("timestamp") or ""),
                updated_at=str(meta.get("timestamp") or ""),
                cwd=str(meta.get("cwd") or ""),
                originators=[str(meta.get("originator") or "")] if meta.get("originator") else [],
                local_rollout=rel,
                rollout_turns=turns, rollout_messages=msgs, rollout_tools=tools,
                catalog_only=False,
            )
            orphans[conv_id] = t
            log(f"chatgpt rollout without catalog row: {conv_id[:8]}", "warn")

    return list(orphans.values()), warnings


# --------------------------------------------------------------------------- #
# 3. browser archaeology
# --------------------------------------------------------------------------- #

def scan_browsers(thread_ids: set[str], tmp_dir: Path,
                  log: LogFn, progress: ProgressFn,
                  cancel: Optional[Callable[[], bool]] = None) -> tuple[list[BrowserTrace], dict[str, tuple[int, str]]]:
    """Scan Chromium browsers for chatgpt.com traces.

    Returns (traces, {thread_id: (visit_count, last_visit)}).
    """
    traces: list[BrowserTrace] = []
    visits: dict[str, tuple[int, str]] = {}
    tmp_dir.mkdir(parents=True, exist_ok=True)
    browsers = _chromium_browsers()
    total = max(sum(len(_profiles(p)) for _, p in browsers), 1)
    done = 0
    for name, user_data in browsers:
        for profile in _profiles(user_data):
            if cancel is not None and cancel():
                return traces, visits
            done += 1
            progress(0.40 + 0.25 * (done / total), f"Browser scan: {name} / {profile.name}")
            trace = BrowserTrace(browser=name, profile=profile.name)

            # -- History sqlite ------------------------------------------------ #
            hist = profile / "History"
            if hist.is_file():
                copy = _copy_temp(hist, tmp_dir)
                if copy is not None:
                    conn = _connect_ro(copy)
                    if conn is not None:
                        try:
                            chrome_epoch = datetime(1601, 1, 1, tzinfo=timezone.utc)
                            for row in conn.execute(
                                "SELECT url, title, visit_count, last_visit_time "
                                "FROM urls WHERE url LIKE '%chatgpt.com%'"
                            ):
                                url = str(row["url"])
                                m = re.search(r"chatgpt\.com/c/([0-9a-fA-F-]{36})", url)
                                if m and m.group(1) in thread_ids:
                                    cnt = int(row["visit_count"] or 0)
                                    last = ""
                                    try:
                                        last = _epoch_to_iso(
                                            chrome_epoch.timestamp()
                                            + int(row["last_visit_time"] or 0) / 1_000_000)
                                    except (ValueError, OSError):
                                        pass
                                    prev = visits.get(m.group(1), (0, ""))
                                    visits[m.group(1)] = (max(prev[0], cnt), last or prev[1])
                                trace.history_hits += 1
                                if row["last_visit_time"] and not trace.last_visit:
                                    try:
                                        trace.last_visit = _epoch_to_iso(
                                            chrome_epoch.timestamp()
                                            + int(row["last_visit_time"]) / 1_000_000)
                                    except (ValueError, OSError):
                                        pass
                        except sqlite3.Error as exc:
                            trace.notes.append(f"history read error: {exc}")
                        finally:
                            conn.close()
                        copy.unlink(missing_ok=True)

            # -- IndexedDB ----------------------------------------------------- #
            idb = profile / "IndexedDB"
            if idb.is_dir():
                for entry in idb.iterdir():
                    if "chatgpt.com" in entry.name.lower():
                        # blob payloads live under <entry>/<entry>.blob/**,
                        # leveldb tables under <entry>/<entry>.leveldb/**
                        for sub in entry.iterdir():
                            if sub.is_dir():
                                trace.indexeddb_bytes += sum(
                                    f.stat().st_size for f in sub.rglob("*") if f.is_file())
                        trace.notes.append(
                            f"IndexedDB {entry.name}: leveldb (snappy-compressed, "
                            "not plaintext-parseable offline)")

            # -- Service Worker cache ------------------------------------------ #
            sw = profile / "Service Worker" / "CacheStorage"
            if sw.is_dir():
                for entry in sw.iterdir():
                    if entry.is_dir():
                        trace.service_worker_caches += 1

            # -- Local Storage (keys only) -------------------------------------- #
            ls = profile / "Local Storage" / "leveldb"
            if ls.is_dir():
                keys = 0
                for f in ls.glob("*.ldb"):
                    try:
                        data = f.read_bytes()
                    except OSError:
                        continue
                    if b"chatgpt.com" in data:
                        keys += data.count(b"chatgpt.com")
                if keys:
                    trace.local_storage_keys = keys
                    trace.notes.append("Local Storage references chatgpt.com (leveldb)")

            has_anything = (trace.history_hits or trace.indexeddb_bytes
                            or trace.local_storage_keys)
            if has_anything:
                traces.append(trace)
                log(f"{name}/{profile.name}: {trace.history_hits} history rows, "
                    f"{trace.indexeddb_bytes // 1024} KB IndexedDB", "ok")
    return traces, visits


# --------------------------------------------------------------------------- #
# 4. export archives
# --------------------------------------------------------------------------- #

def _sha256(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _triage_text(text: str, hit: dict[str, int]) -> list[str]:
    thread_refs: list[str] = []
    for tid in re.findall(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
                          text):
        if tid not in thread_refs:
            thread_refs.append(tid)
    for label, pattern in PII_PATTERNS:
        n = len(pattern.findall(text))
        if n:
            hit[label] = hit.get(label, 0) + n
    return thread_refs


def scan_export(path: Path, max_entries: int = 400,
                text_scan_limit: int = 400_000) -> ExportArtifact:
    """Inventory one export artifact (zip / json / md) with PII triage."""
    art = ExportArtifact(path=path, size_bytes=_safe_size(path))
    try:
        art.sha256 = _sha256(path)
    except OSError:
        pass
    suffix = path.suffix.lower()
    if suffix == ".zip":
        art.kind = "zip"
        try:
            with zipfile.ZipFile(path) as zf:
                names = zf.namelist()
                art.entries = names[:max_entries]
                hit = art.pii_hits
                for name in names[:max_entries]:
                    if re.search(r"sandbox:/|/workspace/scratch/([0-9a-f]{8,})\b", name):
                        m = re.search(r"/workspace/scratch/([0-9a-f]{8,})", name)
                        if m and m.group(1) not in art.sandbox_ids:
                            art.sandbox_ids.append(m.group(1))
                    if name.lower().endswith((".json", ".md", ".txt", ".py", ".js",
                                              ".ts", ".toml", ".yaml", ".yml")):
                        try:
                            data = zf.read(name)[:text_scan_limit]
                            text = data.decode("utf-8", "replace")
                            refs = _triage_text(text, hit)
                            for r in refs:
                                if r not in art.chat_thread_refs:
                                    art.chat_thread_refs.append(r)
                            # sandbox ids also appear in file bodies
                            # (e.g. {"sandbox": "/workspace/scratch/<id>"})
                            for m in re.finditer(
                                    r"/workspace/scratch/([0-9a-f]{8,})", text):
                                if m.group(1) not in art.sandbox_ids:
                                    art.sandbox_ids.append(m.group(1))
                        except (zipfile.BadZipFile, UnicodeError):
                            pass
        except (zipfile.BadZipFile, OSError) as exc:
            art.error = f"zip error: {exc}"
    elif suffix == ".json":
        art.kind = "json"
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[:text_scan_limit]
            art.entries = ["<root>"]
            art.chat_thread_refs = _triage_text(text, art.pii_hits)[:50]
        except OSError as exc:
            art.error = f"read error: {exc}"
    elif suffix in (".md", ".markdown", ".txt"):
        art.kind = "markdown"
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[:text_scan_limit]
            art.entries = [f"{len(text):,} chars"]
            art.chat_thread_refs = _triage_text(text, art.pii_hits)[:50]
        except OSError as exc:
            art.error = f"read error: {exc}"
    else:
        art.kind = "unknown"
    return art


def _safe_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


# --------------------------------------------------------------------------- #
# orchestrator
# --------------------------------------------------------------------------- #

def _noop(msg: str, level: str = "info") -> None:  # pragma: no cover
    pass


def run_chatwork(
    codex_home: Path,
    export_paths: list[Path],
    tmp_dir: Optional[Path] = None,
    log: LogFn = _noop,
    progress: ProgressFn = lambda f, s: None,
    cancel: Optional[Callable[[], bool]] = None,
    scan_browser: bool = True,
    scan_rollout_bodies: bool = True,
) -> ChatWorkReport:
    """Full Chat & Work archaeology run."""
    import tempfile
    codex_home = Path(codex_home)
    if tmp_dir is None:
        tmp_dir = Path(tempfile.mkdtemp(prefix="dekodx_chatwork_"))

    progress(0.02, "Reading thread catalog…")
    log(f"Reading catalog from {codex_home / 'sqlite' / 'codex-dev.db'}")
    threads, hosts, sync, warnings = read_catalog(codex_home)
    log(f"Catalog: {len(threads)} chatgpt.com threads "
        f"({sum(1 for t in threads if t.conversation_origin == 'tpp')} typed-in-ChatGPT)", "ok")
    report = ChatWorkReport(
        source_codex=codex_home, catalog_threads=threads,
        hosts=hosts, sync_state=sync, warnings=warnings)

    # -- global state atoms (bindings, projects, resume tokens) -------------- #
    progress(0.04, "Reading global state atoms…")
    gstate, gs_warnings = read_global_state(codex_home)
    report.global_state = gstate
    report.warnings += gs_warnings
    if gstate.thread_bindings or gstate.projects:
        log(f"Global state: {len(gstate.thread_bindings)} thread bindings, "
            f"{len(gstate.projects)} chatgpt Projects, "
            f"{len(gstate.resume_token_threads)} resume-token thread(s)", "ok")
    # enrich catalog rows with binding knowledge
    if gstate:
        bound = set(gstate.thread_bindings.values())
        for t in threads:
            if t.thread_id in bound:
                t.originators.append("client-binding")

    if scan_rollout_bodies:
        progress(0.05, "Scanning codex rollouts for chatgpt links…")
        orphans, roll_warnings = scan_rollouts(
            codex_home, threads, log=log, progress=progress, cancel=cancel)
        report.rollout_threads = orphans
        report.warnings += roll_warnings
        linked = sum(1 for t in threads if not t.catalog_only)
        log(f"Rollouts: {linked} catalog threads linked, {len(orphans)} orphans", "ok")

    if scan_browser:
        progress(0.40, "Scanning browsers…")
        tid_set = {t.thread_id for t in report.all_threads}
        traces, visits = scan_browsers(tid_set, tmp_dir, log, progress, cancel)
        report.browsers = traces
        for t in report.all_threads:
            if t.thread_id in visits:
                t.browser_visits, t.last_browser_visit = visits[t.thread_id]
        log(f"Browsers: {len(traces)} profile(s) with chatgpt.com data, "
            f"{sum(b.history_hits for b in traces)} history rows", "ok")

    progress(0.68, "Triaging exports…")
    for i, p in enumerate(export_paths, 1):
        if cancel is not None and cancel():
            break
        progress(0.68 + 0.27 * (i / max(len(export_paths), 1)), f"Export {i}/{len(export_paths)}")
        p = Path(p)
        if not p.is_file():
            report.warnings.append(f"export not found: {p}")
            continue
        art = scan_export(p)
        report.exports.append(art)
        log(f"Export {p.name}: {art.kind}, {art.size_bytes:,} B, "
            f"pii={art.pii_hits or 'none'}", "ok" if not art.pii_hits else "warn")

    progress(1.0, "Done")
    return report
