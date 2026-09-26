"""SQLite readers for state_5.sqlite, logs_2.sqlite, sqlite/codex-dev.db,
thread_history_1.sqlite, goals_1.sqlite, queue_1.sqlite, memories_1.sqlite.

All databases are opened read-only (URI mode=ro) so a live Codex install is
never locked or mutated. Schema drift is tolerated: requested columns are
intersected with PRAGMA table_info before querying.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

THREAD_COLUMNS = (
    "id", "rollout_path", "created_at", "updated_at", "source", "model_provider", "cwd",
    "title", "sandbox_policy", "approval_mode", "tokens_used", "has_user_event",
    "archived", "archived_at", "git_sha", "git_branch", "git_origin_url", "cli_version",
    "first_user_message", "agent_nickname", "agent_role", "memory_mode", "model",
    "reasoning_effort", "agent_path", "created_at_ms", "updated_at_ms", "thread_source",
)

LOG_COLUMNS = (
    "id", "ts", "ts_nanos", "level", "target", "feedback_log_body", "module_path",
    "file", "line", "thread_id", "process_uuid", "estimated_bytes",
)

THREAD_HISTORY_COLUMNS = (
    "thread_id", "message_id", "role", "content", "created_at", "turn_id",
    "reasoning_effort", "model", "token_usage",
)

GOALS_COLUMNS = (
    "thread_id", "goal_id", "objective", "status", "token_budget", "tokens_used",
    "time_used_seconds", "created_at_ms", "updated_at_ms",
)


def _connect(path: Path) -> Optional[sqlite3.Connection]:
    try:
        conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        return None


def _tables(conn: sqlite3.Connection) -> set[str]:
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {row["name"] for row in cur.fetchall()}


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    cur = conn.execute(f'PRAGMA table_info("{table}")')
    return [row["name"] for row in cur.fetchall()]


def fetch_table(
    path: Path,
    table: str,
    columns: tuple[str, ...] = (),
    order_by: str = "",
    limit: int | None = None,
) -> list[dict]:
    """Read a table defensively; returns [] when db/table/columns are missing."""
    conn = _connect(path)
    if conn is None:
        return []
    try:
        if table not in _tables(conn):
            return []
        available = _columns(conn, table)
        if not available:
            return []
        selected = [c for c in columns if c in available] or available
        sql = f'SELECT {", ".join(selected)} FROM "{table}"'
        if order_by and order_by.split()[0] in available:
            sql += f" ORDER BY {order_by}"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [dict(row) for row in conn.execute(sql).fetchall()]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def count_rows(path: Path, table: str) -> int:
    conn = _connect(path)
    if conn is None:
        return 0
    try:
        if table not in _tables(conn):
            return 0
        return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
    except sqlite3.Error:
        return 0
    finally:
        conn.close()


def get_user_version(path: Path) -> int:
    """Get PRAGMA user_version from a SQLite database."""
    try:
        conn = _connect(path)
        if conn is None:
            return 0
        row = conn.execute("PRAGMA user_version").fetchone()
        conn.close()
        return row[0] if row else 0
    except:
        return 0


# --------------------------------------------------------------------------- #
# state_5.sqlite
# --------------------------------------------------------------------------- #

def read_threads(path: Path) -> list[dict]:
    return fetch_table(path, "threads", THREAD_COLUMNS, order_by="updated_at")


def read_thread(path: Path, thread_id: str) -> Optional[dict]:
    """Read a single thread by ID."""
    rows = fetch_table(path, "threads", THREAD_COLUMNS)
    for row in rows:
        if row.get("id") == thread_id:
            return row
    return None


def read_dynamic_tools(path: Path) -> list[dict]:
    return fetch_table(
        path, "thread_dynamic_tools",
        ("thread_id", "position", "name", "description", "input_schema", "defer_loading", "namespace"),
        order_by="position",
    )


def read_goals_from_state(path: Path) -> list[dict]:
    """Read goals from state_5.sqlite (thread_goals table)."""
    return fetch_table(path, "thread_goals", GOALS_COLUMNS)


def read_jobs(path: Path) -> list[dict]:
    return fetch_table(
        path, "jobs",
        ("kind", "job_key", "status", "started_at", "finished_at", "retry_remaining", "last_error"),
    )


def read_spawn_edges(path: Path) -> list[dict]:
    """Read parent→child thread spawn relationships."""
    return fetch_table(
        path, "thread_spawn_edges", ("parent_thread_id", "child_thread_id", "status")
    )


def read_enrollments(path: Path) -> list[dict]:
    return fetch_table(
        path, "remote_control_enrollments",
        ("websocket_url", "account_id", "app_server_client_name", "server_id",
         "environment_id", "server_name", "updated_at"),
    )


def read_stage1_outputs(path: Path) -> list[dict]:
    return fetch_table(
        path, "stage1_outputs",
        ("thread_id", "source_updated_at", "rollout_slug", "usage_count", "last_usage",
         "selected_for_phase2", "generated_at"),
    )


# --------------------------------------------------------------------------- #
# logs_2.sqlite
# --------------------------------------------------------------------------- #

def read_logs(path: Path, limit: int = 1000) -> list[dict]:
    rows = fetch_table(path, "logs", LOG_COLUMNS, order_by="id DESC", limit=limit)
    rows.reverse()                       # chronological order for the report
    return rows


# --------------------------------------------------------------------------- #
# thread_history_1.sqlite (newer)
# --------------------------------------------------------------------------- #

def read_thread_history(path: Path, thread_id: str | None = None) -> list[dict]:
    """Read message history from thread_history_1.sqlite."""
    if thread_id:
        return fetch_table(
            path, "messages", THREAD_HISTORY_COLUMNS + tuple(),
            order_by="created_at",
        )
    return fetch_table(path, "messages", THREAD_HISTORY_COLUMNS, order_by="created_at")


def read_thread_history_threads(path: Path) -> list[dict]:
    """Read thread list from thread_history_1.sqlite."""
    return fetch_table(path, "threads", THREAD_COLUMNS, order_by="updated_at")


# --------------------------------------------------------------------------- #
# goals_1.sqlite (newer)
# --------------------------------------------------------------------------- #

def read_goals(path: Path) -> list[dict]:
    """Read goals from goals_1.sqlite."""
    return fetch_table(path, "goals", GOALS_COLUMNS)


# --------------------------------------------------------------------------- #
# queue_1.sqlite (newer)
# --------------------------------------------------------------------------- #

def read_queue(path: Path) -> list[dict]:
    """Read follow-up queue from queue_1.sqlite."""
    return fetch_table(
        path, "queue",
        ("id", "thread_id", "prompt", "status", "created_at", "updated_at"),
        order_by="created_at",
    )


# --------------------------------------------------------------------------- #
# memories_1.sqlite (newer)
# --------------------------------------------------------------------------- #

def read_memories(path: Path) -> list[dict]:
    """Read memories from memories_1.sqlite."""
    return fetch_table(
        path, "memories",
        ("id", "title", "content", "created_at", "updated_at"),
        order_by="updated_at DESC",
    )


# --------------------------------------------------------------------------- #
# sqlite/codex-dev.db
# --------------------------------------------------------------------------- #

DEV_TABLES = ("inbox_items", "automations", "automation_runs", "local_app_server_feature_enablement")


def read_dev_db(path: Path, per_table_limit: int = 50) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    conn = _connect(path)
    if conn is None:
        return out
    try:
        tables = _tables(conn)
        for table in DEV_TABLES:
            if table in tables:
                out[table] = fetch_table(path, table, (), limit=per_table_limit)
    finally:
        conn.close()
    return out
