"""Mock ChatGPT / Chat & Work fixtures for DeKodX testing.

Builds:
  * a fake .codex with sqlite/codex-dev.db local_thread_catalog
    (chatgpt + vscode rows, tpp origin, hosts, sync state)
  * chatgpt-linked rollout JSONLs (session_meta.source='chatgpt',
    chatgpt_conversation_id) incl. one orphan not in the catalog
  * a fake Chromium profile with History (chatgpt.com/c/<tid> rows),
    IndexedDB dir, Local Storage leveldb with a chatgpt key
  * a sandbox-style export zip with PII landmines + sandbox paths
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

T_TPP = "6aae3920-84a0-83ec-bf07-bdfd97b5e5de"      # typed into ChatGPT (tpp)
T_CHAT = "6aac827f-b018-83ec-8c4d-bf3beea1a3e8"     # chatgpt origin, no rollout
T_VSCODE = "6aa95833-1b20-83ec-b584-02115808adb1"   # vscode bridge row
T_ORPHAN = "6aa80a3f-7310-83ec-bca2-f2cfbb8e01b4"   # rollout without catalog row
HOST_CHATGPT = "chatgpt:be2026e4-78e6-42cb-999a-908ae787f19e:user-O7ti4b7hXiEes3oIXGo75S6o"
HOST_LOCAL = "local"

EPOCH_BASE = 1789800000.0     # ~2026-09-19


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_codex(home: Path) -> Path:
    """Fake ~/.codex with catalog + chatgpt-linked rollouts."""
    home.mkdir(parents=True, exist_ok=True)
    db = home / "sqlite" / "codex-dev.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute("""CREATE TABLE local_thread_catalog (
        host_id TEXT, thread_id TEXT, display_title TEXT, source_created_at REAL,
        source_updated_at REAL, cwd TEXT, source_kind TEXT, source_detail TEXT,
        model_provider TEXT, git_branch TEXT, observation_sequence INTEGER,
        missing_candidate INTEGER, thread_source TEXT, source_recency_at REAL,
        pending_observed_title INTEGER, project_id TEXT, conversation_origin TEXT)""")
    rows = [
        (HOST_CHATGPT, T_TPP, "Navier Stokes Audit Plan", EPOCH_BASE, EPOCH_BASE + 6200,
         None, "chatgpt", None, None, None, 60, 0, None, EPOCH_BASE + 6200, 0, None, "tpp"),
        (HOST_CHATGPT, T_CHAT, "MCU Hero Availability Analysis", EPOCH_BASE - 100000,
         EPOCH_BASE - 99000, None, "chatgpt", None, None, None, 30, 0, None,
         EPOCH_BASE - 99000, 0, None, None),
        (HOST_CHATGPT, T_VSCODE, "Create LVS Sampler", EPOCH_BASE - 320000,
         EPOCH_BASE - 319000, "C:\\work\\lvs", "vscode", None, "openai", "main",
         12, 0, "user", EPOCH_BASE - 319000, 0, None, None),
    ]
    cur.executemany("INSERT INTO local_thread_catalog VALUES "
                    "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    cur.execute("CREATE TABLE local_thread_catalog_hosts (host_id TEXT, host_kind TEXT)")
    cur.execute("INSERT INTO local_thread_catalog_hosts VALUES (?,?)", (HOST_LOCAL, "local"))
    cur.execute("INSERT INTO local_thread_catalog_hosts VALUES (?,?)", (HOST_CHATGPT, "chatgpt"))
    cur.execute("""CREATE TABLE local_thread_catalog_sync_state (
        host_id TEXT, watermark_updated_at REAL, initial_build_complete INTEGER,
        observation_sequence INTEGER, last_full_reconciled_at INTEGER)""")
    cur.execute("INSERT INTO local_thread_catalog_sync_state VALUES (?,?,?,?,?)",
                (HOST_CHATGPT, EPOCH_BASE + 6200, 1, 86, 1788548561027))
    conn.commit()
    conn.close()

    # -- rollouts: T_TPP linked + orphan ------------------------------------ #
    def rollout(conv_id: str, cwd: str, turns: int) -> None:
        base = datetime(2026, 9, 19, 6, 30, 0, tzinfo=timezone.utc)
        lines = [json.dumps({"timestamp": _iso(base.timestamp()), "type": "session_meta",
                             "payload": {"id": conv_id, "timestamp": _iso(base.timestamp()),
                                         "cwd": cwd, "source": "chatgpt",
                                         "chatgpt_conversation_id": conv_id,
                                         "chatgpt_account_id": "acct_test",
                                         "originator": "Codex Desktop"}})]
        for n in range(1, turns + 1):
            t = (base + timedelta(seconds=n * 10)).timestamp()
            lines.append(json.dumps({"timestamp": _iso(t), "type": "turn_context",
                                     "payload": {"turn_id": f"t{n}", "model": "gpt-5.4"}}))
            lines.append(json.dumps({"timestamp": _iso(t), "type": "event_msg",
                                     "payload": {"type": "user_message",
                                                 "message": f"chat turn {n}"}}))
            lines.append(json.dumps({"timestamp": _iso(t + 1), "type": "event_msg",
                                     "payload": {"type": "agent_message", "phase": "final",
                                                 "message": f"reply {n}"}}))
        d = home / "sessions" / "2026" / "09" / "19"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"rollout-2026-09-19T06-30-00-{conv_id}.jsonl").write_text(
            "\n".join(lines) + "\n", encoding="utf-8")

    rollout(T_TPP, "C:\\Users\\Alice\\audit", 3)
    rollout(T_ORPHAN, "C:\\Users\\Alice\\scratch", 1)
    # a plain vscode rollout that must NOT be linked
    (home / "sessions" / "2026" / "09" / "18").mkdir(parents=True, exist_ok=True)
    (home / "sessions" / "2026" / "09" / "18" /
     f"rollout-2026-09-18T01-00-00-6aa11111-2222-4333-8444-555555555555.jsonl").write_text(
        json.dumps({"timestamp": "2026-09-18T01:00:00Z", "type": "session_meta",
                    "payload": {"id": "6aa11111-2222-4333-8444-555555555555",
                                "source": "vscode", "cwd": "C:\\x"}}) + "\n",
        encoding="utf-8")

    # -- global state atoms --------------------------------------------------- #
    gs = {
        "electron-persisted-atom-state": {
            f"client-thread-bindings-v1.local-chatgpt:a07e2b6c-1d99-423a-8869-078de496f033": T_CHAT,
            "chatgpt-sidebar-state-v1": {
                "be2026e4-78e6-42cb-999a-908ae787f19e": {
                    "pinnedConversations": [],
                    "pinnedProjects": [],
                    "projects": [
                        {"id": "g-p-6a787653bf188191873519355878d0ff",
                         "name": "Editing Toolcall",
                         "createdAt": "2026-08-09T12:45:07.768460+00:00",
                         "updatedAt": "2026-08-09T12:45:08.203444+00:00"},
                        {"id": "g-p-6a7866299d3881919fcdca588be24447",
                         "name": "LVS Mobile Buildout",
                         "createdAt": "2026-08-09T11:36:09.100000+00:00",
                         "updatedAt": "2026-08-09T11:36:10.100000+00:00"},
                    ],
                },
            },
            "chatgpt-conversation-resume-tokens-v1": {
                json.dumps(["user-TEST", "be2026e4-78e6-42cb-999a-908ae787f19e", T_TPP]):
                    {"expiresAtMs": 1789412666811},
            },
            "chatgpt-last-selected-model-v1": {"slug": "gpt-5-6-thinking",
                                               "thinkingEffort": "extended"},
        },
    }
    (home / ".codex-global-state.json").write_text(
        json.dumps(gs, indent=2), encoding="utf-8")
    return home


def build_browser(user_data: Path) -> Path:
    """Fake Chromium 'Brave' profile with chatgpt.com traces."""
    profile = user_data / "Default"
    profile.mkdir(parents=True, exist_ok=True)
    (user_data / "Local State").write_text("{}", encoding="utf-8")

    # History sqlite (Chromium schema)
    hist = profile / "History"
    conn = sqlite3.connect(hist)
    cur = conn.cursor()
    cur.execute("CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT, title TEXT, "
                "visit_count INTEGER, typed_count INTEGER, last_visit_time INTEGER, "
                "hidden INTEGER)")
    chrome_epoch = datetime(1601, 1, 1, tzinfo=timezone.utc)
    now_us = int((datetime(2026, 9, 19, tzinfo=timezone.utc) - chrome_epoch)
                 .total_seconds() * 1_000_000)
    rows = [
        (1, f"https://chatgpt.com/c/{T_TPP}", "Navier Stokes Audit Plan", 3, 1, now_us, 0),
        (2, f"https://chatgpt.com/c/{T_CHAT}", "MCU Hero Availability Analysis", 1, 0, now_us, 0),
        (3, "https://chatgpt.com/", "ChatGPT", 160, 5, now_us, 0),
        (4, "https://example.com/", "Other", 2, 0, now_us, 0),
    ]
    cur.executemany("INSERT INTO urls VALUES (?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()

    # IndexedDB blob
    idb = profile / "IndexedDB" / "https_chatgpt.com_0.indexeddb.blob" / "3" / "00"
    idb.mkdir(parents=True, exist_ok=True)
    (idb / "4").write_bytes(b"\xff\x11\x02\xaa" + b"x" * 1024)

    # Local Storage leveldb (fake .ldb with chatgpt key bytes)
    ls = profile / "Local Storage" / "leveldb"
    ls.mkdir(parents=True, exist_ok=True)
    (ls / "004159.ldb").write_bytes(b"chatgpt.com:conversation_keys" + b"\x00" * 64)

    sw = profile / "Service Worker" / "CacheStorage" / "abc123"
    sw.mkdir(parents=True, exist_ok=True)
    return user_data


def build_export_zip(path: Path) -> Path:
    """Sandbox-style download bundle with PII + sandbox paths."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("audit/README.md",
                    "# Audit\nContact alice@example.com for details.\n")
        zf.writestr("audit/tasks/A01.md",
                    "Task A01 — check formal proof. See thread 6aae3920-84a0-83ec-bf07-"
                    "bdfd97b5e5de for context.\n")
        zf.writestr("audit/tools/task.py",
                    "KEY = 'sk-proj-abcdef1234567890'\nprint('hi')\n")
        zf.writestr("audit/results/source-inventory.json",
                    json.dumps({"sources": ["lean-repo"], "sandbox":
                                "/workspace/scratch/e8adf3fa411b"}))
    return path


def build_all(root: Path) -> dict:
    """Build the complete fixture tree; returns paths."""
    root.mkdir(parents=True, exist_ok=True)
    out = {
        "codex": build_codex(root / "home" / "Alice" / ".codex"),
        "browser": build_browser(root / "AppData" / "Local" / "BraveSoftware"
                                 / "Brave-Browser" / "User Data"),
        "export": build_export_zip(root / "Desktop" / "navier-stokes-audit.zip"),
    }
    return out


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("./fixture_chatwork"))
    args = parser.parse_args()
    paths = build_all(args.out)
    for k, v in paths.items():
        print(f"{k}: {v}")
