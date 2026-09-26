"""Generate a realistic mock ~/.codex directory for testing DeKodX.

Covers every storage format and every edge case in the spec:
  * 5 indexed sessions (active, archived, empty, missing transcript, large)
  * corrupt JSONL lines, encrypted reasoning, duplicate event/response pairs
  * PII landmines: emails, home paths, SIDs, JWTs, API keys, git creds
  * state_5.sqlite / logs_2.sqlite / sqlite/codex-dev.db with full schemas
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path

HOME_WIN = "C:\\Users\\Alice"
EMAIL = "alice@example.com"
JWT = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9." + "cGF5bG9hZA" + "x" * 40 + "." + "y" * 48
API_KEY = "sk-proj-ABCDEF0123456789ABCDEF0123456789"
SID = "S-1-5-21-3623811015-3362829242-214562772-1001"
INSTALL_ID = str(uuid.uuid4())
GIT_URL = "https://alice:ghp_Token1234567890@github.com/alice/secret-repo.git"
WP_PASS = "supersecret-wp-pass-42"

S1 = "019f2960-aba8-7c70-8601-41bcadccd384"   # rich active session
S2 = "019d90f8-ca66-7a73-9bb6-7dde6509468b"   # archived session
S3 = "019e1111-2222-4333-8444-555555555555"   # empty session
S4 = "019eaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"   # missing transcript
S5 = "019f2963-3afe-7b71-8576-ba2883435f42"   # large session

BASE_INSTRUCTIONS = (
    "You are Codex, a coding agent running inside the Codex Desktop application. "
    "You operate on a local workspace with shell, patch and MCP tools. "
    "Be concise, verify your work, and never leak secrets. "
)


def _ts(base: datetime, seconds: float) -> str:
    return (base + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.") + "000Z"


def _line(ts: str, kind: str, payload: dict) -> str:
    return json.dumps({"timestamp": ts, "type": kind, "payload": payload}, ensure_ascii=False)


def build_session_1(base: datetime) -> list[str]:
    lines = [
        _line(_ts(base, 0), "session_meta", {
            "id": S1, "timestamp": _ts(base, 0), "cwd": HOME_WIN + "\\Desktop\\Website Test",
            "originator": "Codex Desktop", "cli_version": "0.130.0-alpha.5", "source": "vscode",
            "model_provider": "openai",
            "base_instructions": {"text": BASE_INSTRUCTIONS},
            "dynamic_tools": [{"name": "sequential_thinking", "description": "think step by step",
                               "inputSchema": {"type": "object"}}],
        }),
        _line(_ts(base, 1), "turn_context", {
            "turn_id": "t1", "cwd": HOME_WIN + "\\Desktop\\Website Test",
            "current_date": "2026-04-15", "timezone": "Australia/Sydney",
            "approval_policy": "never", "sandbox_policy": {"type": "danger-full-access"},
            "model": "gpt-5.1-codex-mini", "personality": "pragmatic",
            "collaboration_mode": {"mode": "default"}, "effort": "medium", "summary": "none",
            "truncation_policy": {"mode": "tokens", "limit": 10000},
        }),
        _line(_ts(base, 1), "event_msg", {"type": "task_started", "turn_id": "t1",
                                          "started_at": _ts(base, 1), "model_context_window": 258400,
                                          "collaboration_mode_kind": "default"}),
        _line(_ts(base, 2), "event_msg", {"type": "user_message",
                                          "message": f"OK use playwright to access https://example.com/shop/ then navigate to about me then stop then tell me any errors you have encountered. My email is {EMAIL} if you need me.",
                                          "images": [], "local_images": [], "text_elements": []}),
        _line(_ts(base, 2), "response_item", {"type": "message", "role": "user", "content": [
            {"type": "input_text",
             "text": f"OK use playwright to access https://example.com/shop/ then navigate to about me then stop then tell me any errors you have encountered. My email is {EMAIL} if you need me."}]}),
        _line(_ts(base, 4), "event_msg", {"type": "agent_message", "phase": "commentary",
                                          "message": "I'm going to open the browser and navigate to the site..."}),
        _line(_ts(base, 5), "response_item", {"type": "reasoning", "summary": [],
                                              "encrypted_content": "gAAAAABmZmZmZmZm encrypted blob"}),
        _line(_ts(base, 6), "response_item", {"type": "function_call", "name": "browser_tabs",
                                              "namespace": "mcp__playwright_2__",
                                              "arguments": {"action": "list"}, "call_id": "call_aBSvuu4F0001"}),
        _line(_ts(base, 7), "response_item", {"type": "function_call_output", "call_id": "call_aBSvuu4F0001",
                                              "output": "Error reading storage state from " + HOME_WIN + "\\playwright\\state.json: file not found"}),
        _line(_ts(base, 9), "event_msg", {"type": "exec_command_end", "call_id": "call_exec0000001",
                                          "turn_id": "t1", "command": ["ls", "-la"],
                                          "cwd": HOME_WIN + "\\Desktop\\Website Test",
                                          "parsed_cmd": [{"type": "list", "items": ["ls", "-la"]}],
                                          "source": "shell", "stdout": f"total 8\ndrwxr-xr-x 2 Alice 197121 0 Jul 4 05:09 .\nkey={API_KEY}\n",
                                          "stderr": "", "aggregated_output": f"total 8\ndrwxr-xr-x 2 Alice 197121 0 Jul 4 05:09 .\nkey={API_KEY}\n"}),
        _line(_ts(base, 10), "event_msg", {"type": "token_count",
                                           "info": {"total_token_usage": {"input_tokens": 74658, "output_tokens": 625, "total_tokens": 75283},
                                                    "last_token_usage": {"input_tokens": 74658, "output_tokens": 625, "total_tokens": 75283},
                                                    "model_context_window": 258400},
                                           "rate_limits": {"limit_id": "codex", "primary": {"used_percent": 12, "window_minutes": 300, "resets_at": "2026-04-15T12:00:00Z"}, "plan_type": "plus"}}),
        _line(_ts(base, 12), "event_msg", {"type": "agent_message", "phase": "final",
                                           "message": "Done. Here's what I found: the About Me page returns a 404 and the storage state file is missing."}),
        _line(_ts(base, 12), "response_item", {"type": "message", "role": "assistant", "content": [
            {"type": "input_text", "text": "Done. Here's what I found: the About Me page returns a 404 and the storage state file is missing."}]}),
        _line(_ts(base, 13), "event_msg", {"type": "task_complete", "turn_id": "t1",
                                           "last_agent_message": "Done. Here's what I found: the About Me page returns a 404 and the storage state file is missing.",
                                           "completed_at": _ts(base, 13), "duration_ms": 12000,
                                           "time_to_first_token_ms": 900}),
    ]
    # corrupt lines, on purpose
    lines.insert(8, "{ this is not json at all")
    lines.insert(14, "")
    return lines


def build_session_2(base: datetime) -> list[str]:
    return [
        _line(_ts(base, 0), "session_meta", {
            "id": S2, "timestamp": _ts(base, 0), "cwd": HOME_WIN + "\\Documents\\notes",
            "originator": "Codex Desktop", "cli_version": "0.125.0", "source": "cli",
            "model_provider": "openai", "base_instructions": {"text": BASE_INSTRUCTIONS}}),
        _line(_ts(base, 1), "turn_context", {"turn_id": "t1", "model": "gpt-5.4", "effort": "low",
                                             "approval_policy": "on-request",
                                             "sandbox_policy": {"type": "workspace-write"},
                                             "cwd": HOME_WIN + "\\Documents\\notes"}),
        _line(_ts(base, 2), "event_msg", {"type": "user_message", "message": "summarise the meeting notes for me"}),
        _line(_ts(base, 3), "response_item", {"type": "custom_tool_call", "status": "completed",
                                              "call_id": "call_patch000001", "name": "apply_patch",
                                              "input": "*** Begin Patch\n*** Update File: notes.md\n+ summary added\n*** End Patch"}),
        _line(_ts(base, 4), "event_msg", {"type": "patch_apply_end", "call_id": "call_patch000001",
                                          "turn_id": "t1", "file_path": HOME_WIN + "\\Documents\\notes\\notes.md",
                                          "success": True, "error": None}),
        _line(_ts(base, 5), "event_msg", {"type": "agent_message", "phase": "final",
                                          "message": "Summary written to notes.md."}),
        _line(_ts(base, 6), "event_msg", {"type": "task_complete", "turn_id": "t1",
                                          "last_agent_message": "Summary written to notes.md.",
                                          "completed_at": _ts(base, 6), "duration_ms": 4200,
                                          "time_to_first_token_ms": 610}),
    ]


def build_session_5(base: datetime, turns: int = 16) -> list[str]:
    lines = [
        _line(_ts(base, 0), "session_meta", {
            "id": S5, "timestamp": _ts(base, 0), "cwd": "C:\\editing",
            "originator": "Codex Desktop", "cli_version": "0.130.0-alpha.5", "source": "vscode",
            "model_provider": "openai",
            # one very heavy line so the transcript reaches several MB
            "base_instructions": {"text": BASE_INSTRUCTIONS * 90_000},
            "dynamic_tools": [{"name": "process_photo", "description": "analyse or edit a photo",
                               "inputSchema": {"type": "object"}}],
        }),
    ]
    for n in range(1, turns + 1):
        t = float(n * 10)
        lines += [
            _line(_ts(base, t), "turn_context", {"turn_id": f"t{n}", "cwd": "C:\\editing",
                                                 "model": "gpt-5.4-mini", "effort": "high",
                                                 "approval_policy": "never",
                                                 "sandbox_policy": {"type": "danger-full-access"},
                                                 "personality": "friendly",
                                                 "collaboration_mode": {"mode": "default"}}),
            _line(_ts(base, t + 1), "event_msg", {"type": "task_started", "turn_id": f"t{n}",
                                                  "started_at": _ts(base, t + 1),
                                                  "model_context_window": 258400}),
            _line(_ts(base, t + 2), "event_msg", {"type": "user_message",
                                                  "message": f"ok this is test turn {n} of how u autonomously edit photos that have not been edited yet, use sequential thinking"}),
            _line(_ts(base, t + 3), "event_msg", {"type": "agent_message", "phase": "commentary",
                                                  "message": f"I can see the photos directory. Let me check tool availability for pass {n}..."}),
            _line(_ts(base, t + 4), "response_item", {"type": "reasoning", "summary": [],
                                                      "encrypted_content": "gAAAAAB encrypted " * 40}),
            _line(_ts(base, t + 5), "event_msg", {"type": "dynamic_tool_call_request",
                                                  "callId": f"call_dyn{n:08d}", "turnId": f"t{n}",
                                                  "tool": "process_photo",
                                                  "arguments": {"image_path": f"C:\\editing\\photos\\DSC_{n:04d}.jpg",
                                                                "operation": "analyze"}}),
            _line(_ts(base, t + 6), "event_msg", {"type": "dynamic_tool_call_response",
                                                  "callId": f"call_dyn{n:08d}", "turnId": f"t{n}",
                                                  "result": {"luma": 42.6 + n, "cast": "neutral",
                                                             "clipping": 0.01}}),
            _line(_ts(base, t + 7), "event_msg", {"type": "mcp_tool_call_end",
                                                  "call_id": f"call_mcp{n:08d}",
                                                  "tool_name": "sequential_thinking",
                                                  "result": {"thought": f"plan step {n}", "next": "adjust"}}),
            _line(_ts(base, t + 8), "event_msg", {"type": "view_image_tool_call",
                                                  "call_id": f"call_view{n:08d}",
                                                  "path": f"C:\\editing\\photos\\DSC_{n:04d}.jpg"}),
            _line(_ts(base, t + 9), "event_msg", {"type": "token_count",
                                                  "info": {"last_token_usage": {"input_tokens": 185432 - n * 1000,
                                                                                 "output_tokens": 1245 - n * 10,
                                                                                 "total_tokens": 186677 - n * 1010},
                                                           "total_token_usage": {"input_tokens": 185432 * n,
                                                                                 "output_tokens": 1245 * n,
                                                                                 "total_tokens": 186677 * n}}}),
            _line(_ts(base, t + 10), "event_msg", {"type": "agent_message", "phase": "final",
                                                   "message": f"Pass {n} complete: exposure adjusted, white balance neutral."}),
            _line(_ts(base, t + 11), "event_msg", {"type": "task_complete", "turn_id": f"t{n}",
                                                   "last_agent_message": f"Pass {n} complete: exposure adjusted, white balance neutral.",
                                                   "completed_at": _ts(base, t + 11),
                                                   "duration_ms": 9000 + n, "time_to_first_token_ms": 800}),
        ]
    return lines


def write_jsonl(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def build_state_db(path: Path, dates: dict[str, datetime]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute("""CREATE TABLE threads (
        id TEXT PRIMARY KEY, rollout_path TEXT, created_at TEXT, updated_at TEXT, source TEXT,
        model_provider TEXT, cwd TEXT, title TEXT, sandbox_policy TEXT, approval_mode TEXT,
        tokens_used INTEGER, has_user_event INTEGER, archived INTEGER, archived_at TEXT,
        git_sha TEXT, git_branch TEXT, git_origin_url TEXT, cli_version TEXT,
        first_user_message TEXT, agent_nickname TEXT, agent_role TEXT, memory_mode TEXT,
        model TEXT, reasoning_effort TEXT, agent_path TEXT, created_at_ms INTEGER,
        updated_at_ms INTEGER, thread_source TEXT)""")
    rows = [
        (S1, "sessions/2026/04/15/rollout-x.jsonl", dates[S1], dates[S1], "vscode", "openai",
         HOME_WIN + "\\Desktop\\Website Test", "Check About Me navigation", "danger-full-access",
         "never", 1_204_331, 1, 0, None, "abc123", "main", GIT_URL, "0.130.0-alpha.5",
         "OK use playwright...", "Sage", "coder", "enabled", "gpt-5.1-codex-mini", "medium",
         None, 1, 2, "user"),
        (S2, "archived_sessions/rollout-y.jsonl", dates[S2], dates[S2], "cli", "openai",
         HOME_WIN + "\\Documents\\notes", "Summarise meeting notes", "workspace-write",
         "on-request", 88_120, 1, 1, dates[S2].isoformat(), None, None, None, "0.125.0",
         "summarise the meeting notes", None, None, "disabled", "gpt-5.4", "low",
         None, 3, 4, "user"),
        (S3, "sessions/2026/05/01/rollout-z.jsonl", dates[S3], dates[S3], "vscode", "openai",
         "C:\\editing", "Empty scratch session", "workspace-write", "never", 0, 0, 0, None,
         None, None, None, "0.128.0", None, None, None, "enabled", "gpt-5.4-mini", "medium",
         None, 5, 5, "user"),
        (S5, "sessions/2026/07/03/rollout-w.jsonl", dates[S5], dates[S5], "vscode", "openai",
         "C:\\editing", "Test autonomous photo editing", "danger-full-access", "never",
         45_938_961, 1, 0, None, None, None, None, "0.130.0-alpha.5",
         "ok this is a test of how u autonomously edit photos", "Pixel", "editor", "enabled",
         "gpt-5.4-mini", "high", None, 6, 7, "user"),
    ]
    def _iso(v):
        return v.strftime("%Y-%m-%dT%H:%M:%S") if isinstance(v, datetime) else v

    rows = [tuple(_iso(v) for v in r) for r in rows]
    cur.executemany("INSERT INTO threads VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    cur.execute("""CREATE TABLE thread_dynamic_tools (
        thread_id TEXT, position INTEGER, name TEXT, description TEXT, input_schema TEXT,
        defer_loading INTEGER, namespace TEXT)""")
    cur.executemany("INSERT INTO thread_dynamic_tools VALUES (?,?,?,?,?,?,?)", [
        (S1, 0, "sequential_thinking", "think step by step", '{"type":"object"}', 0, None),
        (S5, 0, "process_photo", "analyse or edit a photo", '{"type":"object"}', 0, None),
    ])
    cur.execute("""CREATE TABLE stage1_outputs (
        thread_id TEXT, source_updated_at TEXT, raw_memory TEXT, rollout_summary TEXT,
        generated_at TEXT, rollout_slug TEXT, usage_count INTEGER, last_usage TEXT,
        selected_for_phase2 INTEGER)""")
    cur.execute("INSERT INTO stage1_outputs VALUES (?,?,?,?,?,?,?,?,?)",
                (S5, dates[S5].isoformat(), "user edits photos in C:\\editing", "photo session",
                 dates[S5].isoformat(), "photo-edit", 3, dates[S5].isoformat(), 1))
    cur.execute("""CREATE TABLE jobs (
        kind TEXT, job_key TEXT, status TEXT, worker_id TEXT, ownership_token TEXT,
        started_at TEXT, finished_at TEXT, lease_until TEXT, retry_at TEXT, retry_remaining INTEGER,
        last_error TEXT, input_watermark INTEGER, last_success_watermark INTEGER)""")
    cur.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("memory_generation", S5, "done", "w1", "tok", dates[S5].isoformat(),
                 dates[S5].isoformat(), None, None, 0, None, 10, 10))
    cur.execute("""CREATE TABLE backfill_state (
        id TEXT, status TEXT, last_watermark INTEGER, last_success_at TEXT, updated_at TEXT)""")
    cur.execute("INSERT INTO backfill_state VALUES (?,?,?,?,?)",
                ("threads", "complete", 99, dates[S5].isoformat(), dates[S5].isoformat()))
    cur.execute("CREATE TABLE agent_jobs (id TEXT PRIMARY KEY, kind TEXT, status TEXT)")
    cur.execute("CREATE TABLE agent_job_items (job_id TEXT, position INTEGER, payload TEXT)")
    cur.execute("CREATE TABLE thread_spawn_edges (parent_thread_id TEXT, child_thread_id TEXT, status TEXT)")
    cur.execute("INSERT INTO thread_spawn_edges VALUES (?,?,?)", (S1, S5, "completed"))
    cur.execute("""CREATE TABLE remote_control_enrollments (
        websocket_url TEXT, account_id TEXT, app_server_client_name TEXT, server_id TEXT,
        environment_id TEXT, server_name TEXT, updated_at TEXT)""")
    cur.execute("INSERT INTO remote_control_enrollments VALUES (?,?,?,?,?,?,?)",
                ("wss://rc.example.com/ws/abc123", "acct_9f8e7d6c5b4a", "codex-desktop",
                 "srv_1", "env_prod", "alice-macbook", dates[S5].isoformat()))
    cur.execute("""CREATE TABLE thread_goals (
        thread_id TEXT, goal_id TEXT, objective TEXT, status TEXT, token_budget INTEGER,
        tokens_used INTEGER, time_used_seconds INTEGER, created_at_ms INTEGER, updated_at_ms INTEGER)""")
    cur.execute("INSERT INTO thread_goals VALUES (?,?,?,?,?,?,?,?,?)",
                (S5, "goal_1", "edit every photo in the folder", "in_progress", 50_000_000,
                 45_938_961, 3600, 6, 7))
    conn.commit()
    conn.close()


def build_logs_db(path: Path, base: datetime, rows: int = 1500) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute("""CREATE TABLE logs (
        id INTEGER PRIMARY KEY, ts TEXT, ts_nanos INTEGER, level TEXT, target TEXT,
        feedback_log_body TEXT, module_path TEXT, file TEXT, line INTEGER, thread_id TEXT,
        process_uuid TEXT, estimated_bytes INTEGER)""")
    targets = ["codex_core::session", "codex_cli::ui", "hyper::client", "rustls::conn",
               "codex_core::tools::shell", "codex_mem::stage1"]
    bodies = [
        f"session rollout opened for {S1}",
        "exec command finished exit=0",
        f"storage state read failed for {HOME_WIN}\\playwright\\state.json",
        f"contact {EMAIL} for support",
        "token budget check passed",
        b"binary \x00\x01\x02 payload".decode("utf-8", "replace"),
    ]
    data = []
    for i in range(rows):
        stamp = base + timedelta(seconds=i)
        data.append((i, stamp.strftime("%Y-%m-%dT%H:%M:%S"), i * 1000,
                     ["INFO", "DEBUG", "WARN", "ERROR"][i % 4], targets[i % len(targets)],
                     bodies[i % len(bodies)], "codex_core::session", "session.rs", 100 + i,
                     S1 if i % 3 == 0 else None, f"pid:4321:{uuid.uuid4()}", 256))
    cur.executemany("INSERT INTO logs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", data)
    conn.commit()
    conn.close()


def build_dev_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute("CREATE TABLE inbox_items (id TEXT PRIMARY KEY, kind TEXT, body TEXT, created_at TEXT)")
    cur.execute("INSERT INTO inbox_items VALUES (?,?,?,?)",
                ("in_1", "automation", "nightly photo sweep finished", "2026-07-04T01:00:00"))
    cur.execute("CREATE TABLE automations (id TEXT PRIMARY KEY, name TEXT, schedule TEXT, enabled INTEGER)")
    cur.execute("INSERT INTO automations VALUES (?,?,?,?)", ("auto_1", "nightly photo sweep", "0 1 * * *", 1))
    cur.execute("CREATE TABLE automation_runs (id TEXT PRIMARY KEY, automation_id TEXT, started_at TEXT, status TEXT)")
    cur.execute("INSERT INTO automation_runs VALUES (?,?,?,?)",
                ("run_1", "auto_1", "2026-07-04T01:00:00", "ok"))
    cur.execute("CREATE TABLE local_app_server_feature_enablement (feature TEXT PRIMARY KEY, enabled INTEGER)")
    cur.execute("INSERT INTO local_app_server_feature_enablement VALUES (?,?)", ("memories_v2", 1))
    conn.commit()
    conn.close()


def build(out_dir: Path, big: bool = True) -> Path:
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)

    d1 = datetime(2026, 4, 15, 11, 51, 17)
    d2 = datetime(2026, 4, 20, 9, 12, 0)
    d3 = datetime(2026, 5, 1, 8, 0, 0)
    d5 = datetime(2026, 7, 3, 19, 7, 31)
    dates = {S1: d1, S2: d2, S3: d3, S5: d5}

    (root / "auth.json").write_text(json.dumps({
        "auth_mode": "chatgpt",
        "OPENAI_API_KEY": API_KEY,
        "tokens": {"id_token": JWT, "access_token": JWT[::-1], "refresh_token": "rt_" + "z" * 60,
                   "account_id": "acct_9f8e7d6c5b4a"},
        "last_refresh": "2026-07-03T18:00:00Z",
    }, indent=2), encoding="utf-8")
    (root / "installation_id").write_text(INSTALL_ID + "\n", encoding="utf-8")
    (root / "cap_sid").write_text(SID + "\n", encoding="utf-8")
    (root / "config.toml").write_text(f"""
model = "gpt-5.4-mini"
model_reasoning_effort = "medium"
personality = "pragmatic"

[windows]
sandbox = "elevated"

[mcp_servers.photoedit]
command = ["C:\\\\Python314\\\\python.exe", "C:\\\\editing\\\\photo_server.py"]
enabled = true

[mcp_servers.photoedit.env]
WP_PASS = "{WP_PASS}"

[mcp_servers.playwright]
command = ["npx", "@playwright/mcp"]
enabled = true

[projects."C:\\\\editing"]
trust = true

[marketplaces.openai-bundled]
url = "{GIT_URL}"

[plugins.photo]
version = "1.2.0"

[features]
memories = true
collab = false
undo = true

[memories]
mode = "enabled"
""", encoding="utf-8")
    (root / ".codex-global-state.json").write_text(json.dumps({
        "electron-persisted-atom-state": {
            "prompt-history": [
                f"email me at {EMAIL} afterwards",
                "ok look up what is in C:\\editing",
                "summarise the meeting notes",
            ],
            "heartbeat-thread-permissions-by-id": {},
            "agent-mode-by-host-id": {"desktop-a": "full"},
        },
        "electron-main-window-bounds": {"x": 100, "y": 100, "width": 1400, "height": 900},
        "electron-saved-workspace-roots": [HOME_WIN + "\\Desktop\\Website Test", "C:\\editing"],
        "project-order": ["C:\\editing"],
        "active-workspace-roots": ["C:\\editing"],
        "queued-follow-ups": ["check the about page again"],
    }, indent=2), encoding="utf-8")
    (root / ".codex-global-state.json.bak").write_text("{}", encoding="utf-8")

    write_jsonl(root / "session_index.jsonl", [
        json.dumps({"id": S1, "thread_name": "Check About Me navigation",
                    "updated_at": d1.strftime("%Y-%m-%dT%H:%M:%S.") + "8777145Z"}),
        json.dumps({"id": S2, "thread_name": "Summarise meeting notes",
                    "updated_at": d2.strftime("%Y-%m-%dT%H:%M:%S.") + "0000000Z"}),
        json.dumps({"id": S3, "thread_name": "Empty scratch session",
                    "updated_at": d3.strftime("%Y-%m-%dT%H:%M:%S.") + "0000000Z"}),
        json.dumps({"id": S4, "thread_name": "Ghost session (transcript deleted)",
                    "updated_at": d3.strftime("%Y-%m-%dT%H:%M:%S.") + "0000000Z"}),
        json.dumps({"id": S5, "thread_name": "Test autonomous photo editing",
                    "updated_at": d5.strftime("%Y-%m-%dT%H:%M:%S.") + "0970000Z"}),
    ])

    write_jsonl(root / "sessions" / "2026" / "04" / "15" / f"rollout-2026-04-15T21-48-37-{S1}.jsonl",
                build_session_1(d1))
    write_jsonl(root / "archived_sessions" / f"rollout-2026-04-20T19-12-00-{S2}.jsonl",
                build_session_2(d2))
    write_jsonl(root / "sessions" / "2026" / "05" / "01" / f"rollout-2026-05-01T18-00-00-{S3}.jsonl",
                [_line(_ts(d3, 0), "session_meta", {"id": S3, "timestamp": _ts(d3, 0),
                                                    "cwd": "C:\\editing", "originator": "Codex Desktop",
                                                    "cli_version": "0.128.0", "model_provider": "openai",
                                                    "base_instructions": {"text": BASE_INSTRUCTIONS}})])
    if big:
        write_jsonl(root / "sessions" / "2026" / "07" / "03" / f"rollout-2026-07-04T05-09-56-{S5}.jsonl",
                    build_session_5(d5))

    build_state_db(root / "state_5.sqlite", dates)
    build_logs_db(root / "logs_2.sqlite", d5)
    build_dev_db(root / "sqlite" / "codex-dev.db")

    mem = root / "memories"
    (mem / "extensions" / "ad_hoc").mkdir(parents=True, exist_ok=True)
    (mem / "rollout_summaries").mkdir(parents=True, exist_ok=True)
    (mem / "raw_memories.md").write_text("# Raw memories\n- user edits photos in C:\\editing\n", encoding="utf-8")
    (mem / "extensions" / "ad_hoc" / "instructions.md").write_text("# Ad-hoc\nprefer lossless edits\n", encoding="utf-8")
    (mem / "rollout_summaries" / "photo-edit.md").write_text("# photo edit session\n16 passes\n", encoding="utf-8")
    for sub in ("skills", "plugins", "tmp"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "skills" / "photo.md").write_text("# skill\n", encoding="utf-8")
    (root / "plugins" / "photo.cache").write_text("cache\n", encoding="utf-8")
    (root / "tmp" / "scratch.tmp").write_text("tmp\n", encoding="utf-8")
    return root


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a mock .codex fixture for DeKodX testing")
    parser.add_argument("--out", type=Path, default=Path("./fixture_codex"))
    parser.add_argument("--no-big", action="store_true", help="skip the multi-MB transcript")
    args = parser.parse_args()
    root = build(args.out, big=not args.no_big)
    size = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
    print(f"fixture written to {root} ({size / (1024 * 1024):.1f} MB)")


if __name__ == "__main__":
    main()
