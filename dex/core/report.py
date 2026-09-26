"""Markdown report generation - class-based renderer with per-session output.

Renders a combined recovery report plus individual per-session Markdown files.
Handles subagent hierarchy and version detection display.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from typing import Any, Optional

from .models import RecoveryOptions, SessionRecord, SubagentInfo, Turn
from .redaction import ENCRYPTED_REASONING, Redactor

# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _slug(text: str) -> str:
    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    # Cap slug length to keep filenames OS-friendly (<120 chars)
    if len(slug) > 100:
        slug = slug[:100].rstrip("-_")
    return slug


def fmt_ts(ts: Optional[str], with_time: bool = True) -> str:
    if not ts:
        return "-"
    ts = str(ts).strip()
    base = ts.replace("T", " ")
    if base.endswith("Z"):
        base = base[:-1] + " UTC"
    if not with_time:
        return base.split(" ")[0]
    # Drop the fractional-seconds part (and anything after it) for a clean cell.
    return base.split(".")[0]


def _fmt_mb(size_bytes: int) -> str:
    if size_bytes < 1024 * 1024:
        return f"{max(size_bytes // 1024, 1):,} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def human_size(num_bytes: int | None) -> str:
    """Human-readable file size (B / KB / MB / GB / TB)."""
    if num_bytes is None:
        return "0 B"
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TB"


def _fence(code: str) -> str:
    fence = "```"
    while fence in code:
        fence += "`"
    return fence


def _code_block(code: str, lang: str = "") -> list[str]:
    code = code.rstrip("\n")
    return [_fence(code) + lang, code, _fence(code)]


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    for row in rows:
        cells = [str(c).replace("|", "\\|").replace("\n", " ") for c in row]
        out.append("| " + " | ".join(cells) + " |")
    return out


def _clip_cell(text: str, limit: int = 90) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _mask_filename(rel: str) -> str:
    """rollout-2026-07-04T05-09-56-<uuid>.jsonl -> rollout-...-*.jsonl (spec style)."""
    return _UUID.sub("*", rel or "")


def _most_common_model(sessions: list[SessionRecord]) -> str:
    counter: Counter[str] = Counter()
    for session in sessions:
        if session.model:
            counter[session.model] += 1
        for m in session.models_seen:
            counter[m] += 1
    return counter.most_common(1)[0][0] if counter else ""


def _context_windows(session: SessionRecord) -> str:
    values = []
    for t in session.turns:
        if t.model and t.model not in values:
            pass  # model name, not context window
    if session.context_window:
        values.append(session.context_window)
    return " → ".join(f"{int(v):,}" for v in values if str(v).isdigit()) or "Unknown"


def _summarise_token_payload(payload: dict[str, Any]) -> dict[str, str]:
    """Recursively walk a token_count payload to find input/output/total tokens."""
    nums: dict[str, int] = {}

    def walk(obj: Any, path: str = "") -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                walk(v, f"{path}.{k}" if path else str(k))
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                walk(v, f"{path}.{i}")
        elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
            p = path.lower()
            if "token" in p:
                if "input" in p or "prompt" in p:
                    nums.setdefault("input", int(obj))
                elif "output" in p or "completion" in p:
                    nums.setdefault("output", int(obj))
                elif "total" in p or "used" in p:
                    nums.setdefault("total", int(obj))

    walk(payload.get("info", payload))
    if "total" not in nums and ("input" in nums or "output" in nums):
        nums["total"] = nums.get("input", 0) + nums.get("output", 0)
    return {k: f"{v:,}" for k, v in nums.items()}


_MSG_LABELS = {
    "user": "> **User**:",
    "assistant": "**:",
    "commentary": "**Agent Commentary**:",
    "developer": "**Developer Instructions**:",
    "system": "**System**:",
    "agent": "**Agent**:",
}


# --------------------------------------------------------------------------- #
# MarkdownExporter - class-based renderer
# --------------------------------------------------------------------------- #

class MarkdownExporter:
    """Renders recovery reports with helper methods for consistent formatting."""

    def __init__(self, redactor: Redactor) -> None:
        self.redactor = redactor
        self.lines: list[str] = []

    # -- line helpers ------------------------------------------------------- #

    def _add_block(self, block: str | list[str]) -> None:
        if isinstance(block, list):
            self.lines.extend(block)
        else:
            self.lines.append(block)

    def _h1(self, text: str) -> None:
        self._add_block(f"# {text}")
        self._add_block("")

    def _h2(self, text: str) -> None:
        self._add_block("")
        self._add_block(f"## {text}")
        self._add_block("")

    def _h3(self, text: str) -> None:
        self._add_block("")
        self._add_block(f"### {text}")
        self._add_block("")

    def _h4(self, text: str) -> None:
        self._add_block("")
        self._add_block(f"#### {text}")
        self._add_block("")

    def render(self, bundle: dict) -> str:
        """Render the full combined recovery report."""
        self.lines = []
        options: RecoveryOptions = bundle["options"]
        red: Redactor = bundle["redactor"]
        sessions: list[SessionRecord] = bundle["sessions"]
        stats = bundle["stats"]
        source = str(bundle["source"])
        total = len(sessions)
        dates = [s.updated_at or s.created_at for s in sessions if s.updated_at or s.created_at]
        date_range = f"{min(dates)[:10]} to {max(dates)[:10]}" if dates else "-"

        self._h1("Codex Desktop Session Recovery Report")
        self._add_block(f"**Generated**: {bundle['generated_at']}")
        self._add_block(f"**Source**: `{red.path(source)}`")
        self._add_block(f"**Total Sessions**: {total}")
        self._add_block(f"**Date Range**: {date_range}")
        self._add_block(f"**Redaction Level**: `{options.redaction_level}`")
        self._add_block(f"**Tool**: DeKodX {bundle.get('version', '')}")
        self._add_block("")

        # Version info
        version = bundle.get("version_info")
        if version:
            self._add_block("### Detected Versions")
            self._add_block("")
            if version.app_version:
                self._add_block(f"- **App Version**: {version.app_version}")
            if version.cli_version:
                self._add_block(f"- **CLI Version**: {version.cli_version}")
            if version.desktop_app_version:
                self._add_block(f"- **Desktop App Version**: {version.desktop_app_version}")
            if version.config_format:
                self._add_block(f"- **Config Format**: {version.config_format}")
            if version.rollout_schema_versions:
                self._add_block(f"- **Rollout Schema**: {', '.join(version.rollout_schema_versions)}")
            self._add_block("")

        self._add_block("---")
        self._toc(bundle)
        self._session_index_table(bundle)
        if options.include_app_state and bundle.get("app_state"):
            self._app_state(bundle)
        self._sessions(bundle)
        if options.include_logs and bundle.get("logs"):
            self._logs_section(bundle)
        self._statistics(bundle)
        self._warnings_section(bundle)
        self._add_block("_Report generated locally by DeKodX - no data left this machine._")
        self._add_block("")
        return "\n".join(self.lines).rstrip() + "\n"

    def render_session(self, sess: SessionRecord, bundle: dict) -> str:
        """Render a single session as a standalone Markdown document."""
        self.lines = []
        options: RecoveryOptions = bundle["options"]
        red: Redactor = bundle["redactor"]
        total = len(bundle["sessions"])

        self._h1(f"Session: {sess.title or '(untitled)'}")
        self._add_block(f"**Session ID**: `{red.full_session_id(sess.session_id)}`")
        self._add_block(f"**Generated**: {bundle['generated_at']}")
        self._add_block(f"**Source**: `{red.path(str(bundle['source']))}`")
        self._add_block(f"**Redaction Level**: `{options.redaction_level}`")
        self._add_block(f"**Tool**: DeKodX {bundle.get('version', '')}")
        self._add_block("")
        self._add_block("---")
        self._add_block("")

        # Session fields
        model_cell = sess.model or "-"
        if sess.models_seen and len(sess.models_seen) > 1:
            model_cell = " → ".join(sess.models_seen)
        fields = [
            ("Session ID", f"`{red.full_session_id(sess.session_id)}`"),
            ("Name", red.apply(sess.title or "(untitled)")),
            ("Last active", fmt_ts(sess.updated_at or sess.created_at)),
            ("Workspace", red.path(sess.cwd) or "-"),
            ("Model", model_cell),
        ]
        if sess.context_window:
            fields.append(("Context window", f"{sess.context_window:,} tokens"))
        fields.append(("Source transcript", f"`{_mask_filename(sess.transcript_rel) or 'n/a'}`"))
        fields.append(("Status", sess.status))
        if sess.is_subagent:
            fields.append(("Type", "Subagent"))
        if sess.parent_thread_id:
            fields.append(("Parent thread", red.session_id(sess.parent_thread_id)))
        if sess.agent_nickname:
            fields.append(("Nickname", red.apply(sess.agent_nickname)))
        if sess.agent_role:
            fields.append(("Role", red.apply(sess.agent_role)))
        if sess.size_bytes:
            fields.append(("Transcript size", f"{_fmt_mb(sess.size_bytes)} ({sess.line_count:,} lines)"))
        if sess.reasoning_blocks:
            fields.append(("Reasoning blocks", f"{sess.reasoning_blocks} (all encrypted)"))
        if sess.corrupted_lines:
            fields.append(("Corrupt lines skipped", str(sess.corrupted_lines)))
        if sess.rate_limit_note:
            fields.append(("Rate limits (last)", red.apply(sess.rate_limit_note)))
        if sess.db_row and sess.db_row.get("git_branch"):
            fields.append(("Git branch", red.apply(str(sess.db_row["git_branch"]))))
        if sess.cli_version:
            fields.append(("CLI version", sess.cli_version))

        self._add_block("**Field** | **Value**")
        self._add_block("----------|----------")
        for key, value in fields:
            self._add_block(f"{key} | {value}")
        self._add_block("")

        if sess.transcript_missing:
            self._add_block("> **Transcript missing** - the session index references this session but no "
                       "rollout file exists on disk. Metadata above comes from the index/state database.")
            self._add_block("")
            self._add_block("---")
            self._add_block("")
            return "\n".join(self.lines).rstrip() + "\n"

        # Subagent list
        if sess.has_subagents:
            self._h2("Subagents")
            self._add_block("")
            for sub in sess.subagents:
                self._add_block(f"- **{red.apply(sub.child_title or sub.child_session_id[:12])}** "
                               f"(model: {sub.child_model or '?'}, tokens: {sub.child_tokens:,}, "
                               f"status: {sub.child_status})")
            self._add_block("")
            self._add_block("---")
            self._add_block("")

        if options.include_transcripts:
            if sess.is_empty:
                self._add_block("_(empty session - metadata only, no turns recorded)_")
                self._add_block("")
            else:
                self._h2("Conversation")
                for turn in sess.turns:
                    self._turn_block(turn, options, red)
                self._add_block("---")
                self._add_block("")

            if options.include_tokens:
                rows = []
                for turn in sess.turns:
                    if turn.token_usage:
                        inp, cached, outp, reasoning, tot = turn.token_usage.as_row()
                        rows.append([str(turn.index), inp, cached, outp, reasoning, tot])
                if rows:
                    self._h2("Token Usage")
                    self._add_block("")
                    self._add_block(_table(["Turn", "Input", "Cached", "Output", "Reasoning", "Total"], rows))
                    self._add_block("")

            tally = sess.tool_tally()
            if options.include_tools and tally:
                self._h2("Tools Used")
                self._add_block("")
                self._add_block(_table(["Tool", "Calls", "Namespace"],
                                [[name, str(count), ns] for name, (count, ns) in tally.items()]))
                self._add_block("")
        else:
            self._add_block("_(transcript content excluded by options)_")
            self._add_block("")

        if sess.warnings:
            self._h2("Session Warnings")
            for warning in sess.warnings[:50]:
                self._add_block(f"- {red.apply(warning)}")
            if len(sess.warnings) > 50:
                self._add_block(f"- ... {len(sess.warnings) - 50} more warnings")
            self._add_block("")

        self._add_block("_Report generated locally by DeKodX - no data left this machine._")
        self._add_block("")
        return "\n".join(self.lines).rstrip() + "\n"

    # -- sections ----------------------------------------------------------- #

    def _toc(self, bundle: dict) -> None:
        sessions = bundle["sessions"]
        self._h2("Table of Contents")
        self._add_block("1. [Session Index](#session-index)")
        if bundle.get("app_state"):
            self._add_block("2. [Application State](#application-state)")
        self._add_block("3. [Sessions](#sessions)")
        for sess in sessions[:50]:
            anchor = _slug(f"session {sess.title or sess.session_id}")
            prefix = "🤖 " if sess.is_subagent else "   - "
            self._add_block(f"{prefix}[Session: {sess.title or '(untitled)'}](#{anchor})")
        if len(sessions) > 50:
            self._add_block(f"   - ... {len(sessions) - 50} more sessions")
        if bundle.get("logs"):
            self._add_block("4. [Application Logs](#application-logs)")
        self._add_block("5. [Statistics](#statistics)")
        self._add_block("6. [Parse Warnings](#parse-warnings)")
        self._add_block("")
        self._add_block("---")
        self._add_block("")

    def _session_index_table(self, bundle: dict) -> None:
        red: Redactor = bundle["redactor"]
        rows = []
        for sess in sorted(bundle["sessions"], key=lambda s: s.updated_at or s.created_at, reverse=True):
            tokens = sess.db_tokens_used
            if tokens is None and sess.total_tokens:
                tokens = sess.total_tokens.total_tokens
            type_indicator = "🤖" if sess.is_subagent else "📝"
            rows.append(
                [
                    f"`{red.session_id(sess.session_id)}`",
                    f"{type_indicator} {_clip_cell(sess.title or '(untitled)', 48)}",
                    fmt_ts(sess.updated_at or sess.created_at),
                    sess.model or "-",
                    f"{tokens:,}" if tokens else "0",
                    sess.status,
                ]
            )
        self._h2("Session Index")
        self._add_block("")
        self._add_block(_table(["Session ID", "Title", "Last Active", "Model", "Tokens", "Status"], rows))
        self._add_block("")
        self._add_block("---")
        self._add_block("")

    def _app_state(self, bundle: dict) -> None:
        state = bundle.get("app_state") or {}
        red: Redactor = bundle["redactor"]
        cfg = state.get("config") or {}
        auth = state.get("auth") or {}
        gstate = state.get("global_state") or {}
        self._h2("Application State")
        self._add_block("")
        self._h3("Configuration")
        self._add_block("")
        self._add_block(
            f"- **Model**: {cfg.get('model') or '-'}\n"
            f"- **Reasoning Effort**: {cfg.get('reasoning_effort') or '-'}\n"
            f"- **Personality**: {cfg.get('personality') or '-'}\n"
            f"- **Auth Mode**: {auth.get('auth_mode') or '-'}"
            + (f" (secrets present: {', '.join(auth.get('present_secrets', []))} - all redacted)"
               if auth.get("present_secrets") else "")
        )
        cli_versions = {s.cli_version for s in bundle["sessions"] if s.cli_version}
        if cli_versions:
            self._add_block(f"- **CLI Version**: {', '.join(sorted(cli_versions))}")
        if cfg.get("features"):
            enabled = [k for k, v in cfg["features"].items() if v]
            self._add_block(f"- **Enabled Features**: {', '.join(enabled) or 'none'}")
        if cfg.get("desktop"):
            desktop = cfg["desktop"]
            self._add_block(f"- **Desktop Settings**: conversationDetailMode={desktop.get('conversationDetailMode', 'N/A')}")

        self._add_block("")
        self._h3("Active Workspaces")
        self._add_block("")
        workspaces = gstate.get("workspaces") or []
        if workspaces:
            for w in workspaces:
                self._add_block(f"- {red.path(w)}")
        else:
            self._add_block("- (none recorded)")

        servers = cfg.get("mcp_servers") or []
        if servers:
            self._add_block("")
            self._h3("MCP Servers")
            self._add_block("")
            rows = [
                [s["name"], red.path(s["command"]) or "-", "true" if s["enabled"] else "false",
                 ", ".join(s["env_keys"]) or "-"]
                for s in servers
            ]
            self._add_block(_table(["Name", "Command", "Enabled", "Env vars (values redacted)"], rows))

        if cfg.get("projects"):
            self._add_block("")
            self._h3("Known Projects")
            self._add_block("")
            for p in cfg["projects"]:
                self._add_block(f"- {red.path(p)}")

        prompts = gstate.get("prompt_history") or []
        if prompts:
            self._add_block("")
            self._h3("Recent Prompt History")
            self._add_block("")
            for i, p in enumerate(prompts[-25:], 1):
                self._add_block(f"{i}. {red.apply(p)}")

        enrollments = state.get("enrollments") or []
        if enrollments:
            self._add_block("")
            self._h3("Remote Control Enrollments")
            self._add_block("")
            rows = [
                [red.apply(e.get("server_name") or "-"), red.apply(e.get("websocket_url") or "-"),
                 red.apply(e.get("account_id") or "-"), fmt_ts(e.get("updated_at"))]
                for e in enrollments
            ]
            self._add_block(_table(["Server", "WebSocket URL", "Account ID", "Updated"], rows))

        goals = state.get("goals") or []
        if goals:
            self._add_block("")
            self._h3("Thread Goals")
            self._add_block("")
            rows = [
                [red.session_id(str(g.get("thread_id") or "")), red.apply(g.get("objective") or "-"),
                 str(g.get("status") or "-"), f"{int(g.get('tokens_used') or 0):,}",
                 f"{int(g.get('token_budget') or 0):,}"]
                for g in goals
            ]
            self._add_block(_table(["Thread", "Objective", "Status", "Tokens Used", "Budget"], rows))

        dev_db = state.get("dev_db") or {}
        if dev_db:
            self._add_block("")
            self._h3("Dev-Mode Database (codex-dev.db)")
            self._add_block("")
            for table, rows_data in dev_db.items():
                self._add_block(f"- `{table}`: {len(rows_data)} row(s) sampled")

        memories = state.get("memories") or []
        if memories:
            self._add_block("")
            self._h3("Memory Files Present")
            self._add_block("")
            for m in memories:
                self._add_block(f"- {red.path(m)}")

        # Newer state
        if state.get("queue"):
            self._add_block("")
            self._h3("Follow-up Queue")
            self._add_block("")
            for q in state["queue"][:10]:
                self._add_block(f"- `{q.get('thread_id', '?')[:12]}...`: {red.apply(q.get('prompt', '')[:80])}")

        if state.get("memories_rows"):
            self._add_block("")
            self._h3("Memories")
            self._add_block("")
            for m in state["memories_rows"][:10]:
                self._add_block(f"- **{red.apply(m.get('title', '(untitled)'))}**: {red.apply(m.get('content', '')[:120])}")

        self._add_block("")
        self._add_block("---")
        self._add_block("")

    def _sessions(self, bundle: dict) -> None:
        self._h2("Sessions")
        self._add_block("")
        if not bundle["sessions"]:
            self._add_block("_(no sessions found)_")
            self._add_block("")
            return
        for sess in bundle["sessions"]:
            self._session_section(sess, bundle["options"], bundle["redactor"])

    def _session_section(self, sess: SessionRecord, options: RecoveryOptions, red: Redactor) -> None:
        title = sess.title or "(untitled)"
        anchor = _slug(f"session {title}")
        self._add_block(f'<a id="{anchor}"></a>')
        type_emoji = "🤖 " if sess.is_subagent else ""
        self._h3(f"{type_emoji}Session: {title}")
        self._add_block("")
        model_cell = sess.model or "-"
        if sess.models_seen and len(sess.models_seen) > 1:
            model_cell = " → ".join(sess.models_seen)
        fields = [
            ("Session ID", f"`{red.full_session_id(sess.session_id)}`"),
            ("Name", red.apply(title)),
            ("Last active", fmt_ts(sess.updated_at or sess.created_at)),
            ("Workspace", red.path(sess.cwd) or "-"),
            ("Model", model_cell),
        ]
        if sess.context_window:
            fields.append(("Context window", f"{sess.context_window:,} tokens"))
        fields.append(("Source transcript", f"`{_mask_filename(sess.transcript_rel) or 'n/a'}`"))
        fields.append(("Status", sess.status))
        if sess.is_subagent:
            fields.append(("Type", "Subagent"))
        if sess.parent_thread_id:
            fields.append(("Parent thread", red.session_id(sess.parent_thread_id)))
        if sess.agent_nickname:
            fields.append(("Nickname", red.apply(sess.agent_nickname)))
        if sess.agent_role:
            fields.append(("Role", red.apply(sess.agent_role)))
        if sess.size_bytes:
            fields.append(("Transcript size", f"{_fmt_mb(sess.size_bytes)} ({sess.line_count:,} lines)"))
        if sess.reasoning_blocks:
            fields.append(("Reasoning blocks", f"{sess.reasoning_blocks} (all encrypted)"))
        if sess.corrupted_lines:
            fields.append(("Corrupt lines skipped", str(sess.corrupted_lines)))
        if sess.rate_limit_note:
            fields.append(("Rate limits (last)", red.apply(sess.rate_limit_note)))
        if sess.db_row and sess.db_row.get("git_branch"):
            fields.append(("Git branch", red.apply(str(sess.db_row["git_branch"]))))
        if sess.cli_version:
            fields.append(("CLI version", sess.cli_version))

        self._add_block("**Field** | **Value**")
        self._add_block("----------|----------")
        for key, value in fields:
            self._add_block(f"{key} | {value}")
        self._add_block("")

        if sess.transcript_missing:
            self._add_block("> **Transcript missing** - the session index references this session but no "
                       "rollout file exists on disk. Metadata above comes from the index/state database.")
            self._add_block("")
            self._add_block("---")
            self._add_block("")
            return

        # Subagent list
        if sess.has_subagents:
            self._h4("Subagents")
            self._add_block("")
            for sub in sess.subagents:
                self._add_block(f"- **{red.apply(sub.child_title or sub.child_session_id[:12])}** "
                               f"(model: {sub.child_model or '?'}, tokens: {sub.child_tokens:,}, "
                               f"status: {sub.child_status})")
            self._add_block("")

        if options.include_transcripts:
            if sess.is_empty:
                self._add_block("_(empty session - metadata only, no turns recorded)_")
                self._add_block("")
            else:
                self._h4("Conversation")
                self._add_block("")
                for turn in sess.turns:
                    self._turn_block(turn, options, red)
                self._add_block("---")
                self._add_block("")

            if options.include_tokens:
                rows = []
                for turn in sess.turns:
                    if turn.token_usage:
                        inp, cached, outp, reasoning, tot = turn.token_usage.as_row()
                        rows.append([str(turn.index), inp, cached, outp, reasoning, tot])
                if rows:
                    self._h4("Token Usage")
                    self._add_block("")
                    self._add_block(_table(["Turn", "Input", "Cached", "Output", "Reasoning", "Total"], rows))
                    self._add_block("")

            tally = sess.tool_tally()
            if options.include_tools and tally:
                self._h4("Tools Used")
                self._add_block("")
                self._add_block(_table(["Tool", "Calls", "Namespace"],
                                [[name, str(count), ns] for name, (count, ns) in tally.items()]))
                self._add_block("")
        else:
            self._add_block("_(transcript content excluded by options)_")
            self._add_block("")

        if sess.warnings:
            self._h4("Session Warnings")
            self._add_block("")
            for warning in sess.warnings[:50]:
                self._add_block(f"- {red.apply(warning)}")
            if len(sess.warnings) > 50:
                self._add_block(f"- ... {len(sess.warnings) - 50} more warnings")
            self._add_block("")
        self._add_block("---")
        self._add_block("")

    def _turn_block(self, turn: Turn, options: RecoveryOptions, red: Redactor) -> list[str]:
        stamp = fmt_ts(turn.started_at)
        head = f"**Turn {turn.index}**" + (f" ({stamp})" if stamp != "-" else "")
        lines = [head, ""]
        meta_bits = []
        if turn.model:
            meta_bits.append(f"model `{turn.model}`")
        if turn.effort and turn.effort != "none":
            meta_bits.append(f"effort `{turn.effort}`")
        if turn.sandbox_policy:
            meta_bits.append(f"sandbox `{turn.sandbox_policy}`")
        if turn.approval_policy:
            meta_bits.append(f"approval `{turn.approval_policy}`")
        if meta_bits:
            lines.append("_" + " · ".join(meta_bits) + "_")
            lines.append("")

        for kind, item in turn.items:
            if kind == "msg":
                label = _MSG_LABELS.get(item.role, f"**{item.role}**:")
                text = red.apply(item.text)
                if item.author:
                    label = f"{label} [from {red.apply(item.author)}]"
                if label.startswith(">"):
                    lines += [f"{label} {line}" if i == 0 else f"> {line}"
                              for i, line in enumerate(text.splitlines()) or [""]]
                    lines.append("")
                else:
                    lines.append(f"{label} {text}")
                    lines.append("")
            elif kind == "tool" and options.include_tools:
                cid = f" `{red.call_id(item.call_id)}`" if item.call_id else ""
                lines.append(f"[Tool Call: `{item.name}`]{cid} _(namespace: {item.namespace or '-'}, kind: {item.kind})_")
                if item.arguments:
                    lines.append("")
                    lines += _code_block(red.apply(item.arguments), "json")
                if item.output:
                    lines.append("")
                    lines.append("[Tool Output]:")
                    lines += _code_block(red.apply(item.output))
                if item.success is not None:
                    lines.append("")
                    lines.append(f"[Patch success: {'yes' if item.success else 'no'}]")
                lines.append("")
            elif kind == "reasoning":
                if item:
                    lines.append(f"[Reasoning summary]: {red.apply(item)}")
                    lines.append("")
                lines.append(f"[Reasoning: {ENCRYPTED_REASONING}]")
                lines.append("")
            elif kind == "error":
                lines.append(f"[Error]: {red.apply(item)}")
                lines.append("")

        if not turn.items:
            lines.append("_(no content recorded for this turn)_")
            lines.append("")

        # Inlined into the report
        self.lines += lines
        return lines

    def _logs_section(self, bundle: dict) -> None:
        red: Redactor = bundle["redactor"]
        logs = bundle.get("logs") or []
        self._h2("Application Logs")
        self._add_block("")
        self._add_block(f"_Last {len(logs)} entries from `logs_2.sqlite` (verbose tracing filtered)._")
        self._add_block("")
        rows = []
        for row in logs:
            body = row.get("feedback_log_body")
            if not body:
                bits = [str(row.get(k) or "") for k in ("module_path", "file") if row.get(k)]
                body = " ".join(bits)
            target = str(row.get("target") or "")
            if target.startswith(("codex_core::log", "hyper", "reqwest", "rustls", "mio")):
                continue
            rows.append(
                [
                    fmt_ts(row.get("ts")),
                    str(row.get("level") or "-"),
                    _clip_cell(target, 40),
                    _clip_cell(red.apply(body), 160),
                ]
            )
        if rows:
            self._add_block(_table(["Timestamp", "Level", "Target", "Message"], rows))
        else:
            self._add_block("_(no relevant log entries)_")
        self._add_block("")
        self._add_block("---")
        self._add_block("")

    def _statistics(self, bundle: dict) -> None:
        stats = bundle["stats"]
        self._h2("Statistics")
        self._add_block("")
        self._add_block(
            f"- **Total sessions**: {stats.get('total_sessions', 0)}\n"
            f"- **Archived**: {stats.get('archived', 0)}\n"
            f"- **Active**: {stats.get('active', 0)}\n"
            f"- **Subagents**: {stats.get('subagents', 0)}\n"
            f"- **Parent sessions**: {stats.get('parents', 0)}\n"
            f"- **Total tokens used**: {stats.get('total_tokens', 0):,}\n"
            f"- **Most used model**: {stats.get('most_used_model', '-')}\n"
            f"- **Date range**: {stats.get('date_from', '-')} to {stats.get('date_to', '-')}\n"
            f"- **User/assistant messages**: {stats.get('messages', 0):,}\n"
            f"- **Tool calls**: {stats.get('tool_calls', 0):,}\n"
            f"- **Encrypted reasoning blocks**: {stats.get('reasoning_blocks', 0):,}\n"
            f"- **Transcript bytes parsed**: {stats.get('transcript_bytes', 0):,} "
            f"({_fmt_mb(stats.get('transcript_bytes', 0))})\n"
            f"- **Corrupt lines skipped**: {stats.get('corrupt_lines', 0)}\n"
            f"- **Missing transcripts**: {stats.get('missing_transcripts', 0)}"
        )
        self._add_block("")
        self._add_block("---")
        self._add_block("")

    def _warnings_section(self, bundle: dict) -> None:
        self._h2("Parse Warnings")
        self._add_block("")
        warnings = bundle.get("warnings") or []
        if warnings:
            for w in warnings[:100]:
                self._add_block(f"- {w}")
            if len(warnings) > 100:
                self._add_block(f"- ... {len(warnings) - 100} more warnings")
        else:
            self._add_block("_(no warnings)_")
        self._add_block("")
        self._add_block("---")
        self._add_block("")


def now_stamp() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")


def build_report(bundle: dict) -> str:
    """Build the combined recovery report."""
    redactor: Redactor = bundle["redactor"]
    exporter = MarkdownExporter(redactor)
    return exporter.render(bundle)


def build_session_report(sess: SessionRecord, bundle: dict) -> str:
    """Build a single-session report."""
    redactor: Redactor = bundle["redactor"]
    exporter = MarkdownExporter(redactor)
    return exporter.render_session(sess, bundle)
