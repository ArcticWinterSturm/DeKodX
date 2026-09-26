"""Data models shared by the parsers, the report writer and the UI."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# --------------------------------------------------------------------------- #
# Transcript models
# --------------------------------------------------------------------------- #


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    reasoning_output_tokens: int = 0
    cached_input_tokens: int = 0

    def as_row(self) -> tuple[str, str, str, str, str]:
        return (
            f"{self.input_tokens:,}",
            f"{self.cached_input_tokens:,}",
            f"{self.output_tokens:,}",
            f"{self.reasoning_output_tokens:,}",
            f"{self.total_tokens:,}",
        )

    def merge(self, other: "TokenUsage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.total_tokens += other.total_tokens
        self.reasoning_output_tokens += other.reasoning_output_tokens
        self.cached_input_tokens += other.cached_input_tokens


@dataclass
class ToolCall:
    name: str
    kind: str = "function"          # function | custom | mcp | dynamic | shell | patch | view_image
    namespace: str = "-"
    call_id: str = ""
    arguments: str = ""
    output: str = ""
    success: Optional[bool] = None


@dataclass
class Message:
    role: str                        # user | assistant | developer | system | commentary | agent
    text: str
    timestamp: str = ""
    author: str = ""                 # for inter-agent messages
    recipient: str = ""              # for inter-agent messages
    phase: str = ""                  # commentary | final


# Item tuples stored inside a Turn, in chronological order:
#   ("msg", Message) | ("tool", ToolCall) | ("reasoning", str) | ("error", str)
TurnItem = tuple


@dataclass
class Turn:
    index: int
    turn_id: str = ""
    started_at: str = ""
    ended_at: str = ""
    duration_ms: Optional[int] = None
    time_to_first_token_ms: Optional[int] = None
    model: str = ""
    effort: str = ""
    personality: str = ""
    approval_policy: str = ""
    sandbox_policy: str = ""
    collaboration_mode: str = ""
    items: list[TurnItem] = field(default_factory=list)
    token_usage: Optional[TokenUsage] = None
    reasoning_count: int = 0

    # -- convenience views -------------------------------------------------- #
    def messages(self) -> list[Message]:
        return [item for kind, item in self.items if kind == "msg"]

    def tool_calls(self) -> list[ToolCall]:
        return [item for kind, item in self.items if kind == "tool"]

    def errors(self) -> list[str]:
        return [item for kind, item in self.items if kind == "error"]


@dataclass
class SubagentInfo:
    """Links a parent session to its child subagent sessions."""
    child_session_id: str
    child_title: str = ""
    child_nickname: str = ""
    child_model: str = ""
    child_tokens: int = 0
    child_status: str = ""           # active | archived | missing
    task_name: str = ""              # e.g. "/root/android_return"
    transcript_path: Optional[Path] = None


@dataclass
class SessionRecord:
    session_id: str
    title: str = ""
    archived: bool = False
    transcript_rel: str = ""
    transcript_path: Optional[Path] = None
    transcript_missing: bool = False
    size_bytes: int = 0
    line_count: int = 0
    corrupted_lines: int = 0
    created_at: str = ""
    updated_at: str = ""
    cwd: str = ""
    model: str = ""
    models_seen: list[str] = field(default_factory=list)
    cli_version: str = ""
    originator: str = ""
    source: str = ""                 # vscode | cli | guardian_review | user
    thread_source: str = ""          # user | guardian_review | subagent
    model_provider: str = ""
    context_window: int = 0
    turns: list[Turn] = field(default_factory=list)
    reasoning_blocks: int = 0
    rate_limit_note: str = ""
    total_tokens: Optional[TokenUsage] = None
    db_tokens_used: Optional[int] = None
    db_row: Optional[dict] = None
    index_row: Optional[dict] = None
    warnings: list[str] = field(default_factory=list)

    # -- subagent linkage -------------------------------------------------- #
    parent_thread_id: str = ""
    parent_session: Optional["SessionRecord"] = None
    subagents: list[SubagentInfo] = field(default_factory=list)
    is_subagent: bool = False
    agent_nickname: str = ""
    agent_role: str = ""

    # -- version detection ------------------------------------------------- #
    app_version: str = ""            # e.g. "26.901.31953" from config
    rollout_schema_version: str = "" # detected from JSONL line types

    @property
    def status(self) -> str:
        return "Archived" if self.archived else "Active"

    @property
    def is_empty(self) -> bool:
        return not self.turns or all(not t.items for t in self.turns)

    @property
    def has_subagents(self) -> bool:
        return len(self.subagents) > 0

    def tool_tally(self) -> dict[str, list]:
        """name -> [calls, namespace]"""
        tally: dict[str, list] = {}
        for turn in self.turns:
            for tc in turn.tool_calls():
                row = tally.setdefault(tc.name, [0, tc.namespace or "-"])
                row[0] += 1
                if tc.namespace and tc.namespace != "-":
                    row[1] = tc.namespace
        return dict(sorted(tally.items(), key=lambda kv: -kv[1][0]))

    def message_count(self) -> int:
        return sum(len(t.messages()) for t in self.turns)


# --------------------------------------------------------------------------- #
# Scan / run models
# --------------------------------------------------------------------------- #


@dataclass
class VersionInfo:
    """Detected versions from the Codex installation."""
    cli_version: str = ""            # from session_meta or config
    app_version: str = ""            # e.g. "26.901.31953" from config
    desktop_app_version: str = ""    # from .codex-global-state.json
    rollout_schema_versions: list[str] = field(default_factory=list)  # detected from files
    state_db_version: str = ""       # from PRAGMA user_version
    logs_db_version: str = ""
    config_format: str = ""          # "v1" (old) | "v2" (new with chatgpt fields)

    @property
    def display_version(self) -> str:
        parts = []
        if self.app_version:
            parts.append(f"app {self.app_version}")
        if self.cli_version:
            parts.append(f"cli {self.cli_version}")
        if self.desktop_app_version:
            parts.append(f"desktop {self.desktop_app_version}")
        return " · ".join(parts) if parts else "unknown"


@dataclass
class ScanReport:
    source: Path
    exists: bool = False
    looks_like_codex: bool = False
    present_files: dict[str, bool] = field(default_factory=dict)
    index_entries: list[dict] = field(default_factory=list)
    transcript_files: list[Path] = field(default_factory=list)
    archived_files: list[Path] = field(default_factory=list)
    has_state_db: bool = False
    has_logs_db: bool = False
    has_dev_db: bool = False
    has_thread_history_db: bool = False
    has_goals_db: bool = False
    has_queue_db: bool = False
    has_memories_db: bool = False
    issues: list[str] = field(default_factory=list)
    version: VersionInfo = field(default_factory=VersionInfo)
    subagent_candidates: list[dict] = field(default_factory=list)

    @property
    def session_count(self) -> int:
        return max(len(self.index_entries), len(self.transcript_files) + len(self.archived_files))


@dataclass
class RecoveryOptions:
    include_transcripts: bool = True
    include_app_state: bool = True
    include_tokens: bool = True
    include_tools: bool = True
    include_logs: bool = True
    log_limit: int = 1000
    redaction_level: str = "standard"     # standard | paranoid | none
    per_sessions: bool = True             # write individual .md per session
    include_subagents: bool = True        # include subagent sessions in reports
    flatten_subagents: bool = False       # show subagent turns inline in parent

    LEVELS = ("standard", "paranoid", "none")


@dataclass
class RecoveryResult:
    output_path: Path
    sessions: list[SessionRecord] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    cancelled: bool = False
    per_session_paths: list[Path] = field(default_factory=list)
    version: VersionInfo = field(default_factory=VersionInfo)


class RecoveryCancelled(Exception):
    """Raised internally when the user cancels a running recovery."""
