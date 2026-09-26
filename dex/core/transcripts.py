"""Streaming parser for Codex session transcripts (JSONL rollouts).

Files can be 35+ MB, so parsing is strictly line-by-line with periodic
progress callbacks. Corrupt lines are counted and skipped, never fatal.

Handles both v1 (classic Codex) and v2 (ChatGPT-integrated) schema:
- v1: event_msg with type=user_message, agent_message, token_count, etc.
- v2: response_item with type=agent_message, custom_tool_call, custom_tool_call_output
       inter_agent_communication_metadata, token_usage_record
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Optional

from .models import Message, SessionRecord, SubagentInfo, TokenUsage, ToolCall, Turn

PROGRESS_BYTES = 2 * 1024 * 1024          # report progress every 2 MB read
MAX_STORED_TEXT = 60_000                 # safety cap per stored message body
MAX_STORED_OUTPUT = 20_000               # safety cap per stored tool output


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… [truncated {len(text) - limit:,} chars]"


def _pretty(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


def _content_text(content) -> str:
    """Extract text from a message content field (string or list of items)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif item.get("type") in ("input_image", "image"):
                    parts.append("[image attached]")
                elif item.get("type") == "encrypted_content":
                    parts.append("[ENCRYPTED]")
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return _pretty(content)


def _extract_usage(info) -> Optional[TokenUsage]:
    """Extract token usage from various possible structures."""
    if not isinstance(info, dict):
        return None
    for key in ("last_token_usage", "total_token_usage"):
        usage = info.get(key)
        if isinstance(usage, dict):
            return TokenUsage(
                int(usage.get("input_tokens") or 0),
                int(usage.get("output_tokens") or 0),
                int(usage.get("total_tokens") or 0),
                int(usage.get("reasoning_output_tokens") or 0),
                int(usage.get("cached_input_tokens") or 0),
            )
    # fall back: recursive hunt for a dict carrying token keys
    stack = [info]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if "input_tokens" in node or "output_tokens" in node:
                return TokenUsage(
                    int(node.get("input_tokens") or 0),
                    int(node.get("output_tokens") or 0),
                    int(node.get("total_tokens") or node.get("input_tokens") or 0)
                    + int(node.get("output_tokens") or 0)
                    if "total_tokens" not in node
                    else int(node.get("total_tokens") or 0),
                    int(node.get("reasoning_output_tokens") or 0),
                    int(node.get("cached_input_tokens") or 0),
                )
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return None


class _TranscriptParser:
    def __init__(self, record: SessionRecord):
        self.rec = record
        self.turn: Optional[Turn] = None
        self._seen: set[tuple[str, str]] = set()
        self._schema_version = "v1"

    # -- turn bookkeeping --------------------------------------------------- #
    def _new_turn(self, turn_id: str = "") -> Turn:
        self.turn = Turn(index=len(self.rec.turns) + 1, turn_id=turn_id)
        self.rec.turns.append(self.turn)
        return self.turn

    def ensure_turn(self, turn_id: str = "") -> Turn:
        if self.turn is None:
            return self._new_turn(turn_id)
        if turn_id and self.turn.turn_id and turn_id != self.turn.turn_id:
            return self._new_turn(turn_id)
        if turn_id and not self.turn.turn_id:
            self.turn.turn_id = turn_id
        return self.turn

    def force_turn(self, turn_id: str) -> Turn:
        """turn_context always opens a fresh turn."""
        return self._new_turn(turn_id)

    # -- item helpers ------------------------------------------------------- #
    def add_message(self, role: str, text: str, ts: str = "", author: str = "", recipient: str = "", phase: str = "") -> None:
        text = (text or "").strip()
        if not text:
            return
        turn = self.ensure_turn()
        key = (role, text[:400])
        if key in self._seen:            # event_msg and response_item duplicate
            return
        self._seen.add(key)
        turn.items.append(("msg", Message(role=role, text=_clip(text, MAX_STORED_TEXT), timestamp=ts, author=author, recipient=recipient, phase=phase)))

    def add_error(self, text: str, ts: str = "") -> None:
        turn = self.ensure_turn()
        turn.items.append(("error", _clip(text or "unknown error", 4000)))

    def add_reasoning(self, summary_text: str) -> None:
        turn = self.ensure_turn()
        self.rec.reasoning_blocks += 1
        turn.reasoning_count += 1
        turn.items.append(("reasoning", summary_text or ""))

    def find_call(self, call_id: str) -> Optional[ToolCall]:
        if not call_id:
            return None
        for turn in reversed(self.rec.turns):
            for tc in turn.tool_calls():
                if tc.call_id == call_id:
                    return tc
        return None

    def add_call(self, call: ToolCall) -> ToolCall:
        self.ensure_turn().items.append(("tool", call))
        return call

    def call_or_create(self, call_id: str, name: str, kind: str, namespace: str = "-") -> ToolCall:
        existing = self.find_call(call_id)
        if existing is not None:
            if name and existing.name in ("", "(unknown)"):
                existing.name = name
            return existing
        return self.add_call(ToolCall(name=name or "(unknown)", kind=kind, namespace=namespace, call_id=call_id))

    # -- line dispatch ------------------------------------------------------ #
    def handle(self, obj: dict, ts: str) -> None:
        kind = obj.get("type")
        payload = obj.get("payload") or {}
        if kind == "session_meta":
            self._meta(payload, ts)
        elif kind == "turn_context":
            self._turn_context(payload, ts)
        elif kind == "event_msg":
            self._event(payload, ts)
        elif kind == "response_item":
            self._response(payload, ts)
        elif kind == "inter_agent_communication_metadata":
            self._inter_agent(payload, ts)
        elif kind == "token_usage_record":
            self._token_usage_record(payload, ts)
        # unknown line types are ignored on purpose (forward compatibility)

    def _meta(self, p: dict, ts: str) -> None:
        rec = self.rec
        rec.session_id = p.get("id") or rec.session_id
        rec.created_at = p.get("timestamp") or ts or rec.created_at
        rec.cwd = p.get("cwd") or rec.cwd
        rec.originator = p.get("originator") or rec.originator
        rec.cli_version = p.get("cli_version") or rec.cli_version
        rec.source = p.get("source") or rec.source
        rec.model_provider = p.get("model_provider") or rec.model_provider
        rec.thread_source = p.get("thread_source") or ""
        rec.parent_thread_id = p.get("parent_thread_id") or ""
        rec.agent_nickname = p.get("agent_nickname") or ""
        rec.agent_role = p.get("agent_role") or ""

        # Detect subagent
        source_info = p.get("source", {})
        if isinstance(source_info, dict) and source_info.get("subagent"):
            rec.is_subagent = True
        if rec.thread_source == "guardian_review":
            rec.is_subagent = True
        if rec.parent_thread_id:
            rec.is_subagent = True

    def _turn_context(self, p: dict, ts: str) -> None:
        turn = self.force_turn(p.get("turn_id") or "")
        turn.started_at = ts
        turn.model = p.get("model") or ""
        turn.effort = p.get("effort") or ""
        turn.personality = p.get("personality") or ""
        turn.approval_policy = p.get("approval_policy") or ""
        sandbox = p.get("sandbox_policy")
        turn.sandbox_policy = sandbox.get("type") if isinstance(sandbox, dict) else _pretty(sandbox)
        mode = p.get("collaboration_mode")
        turn.collaboration_mode = mode.get("mode") if isinstance(mode, dict) else _pretty(mode)
        if turn.model and turn.model not in self.rec.models_seen:
            self.rec.models_seen.append(turn.model)
        if turn.model:
            self.rec.model = turn.model
        if p.get("cwd"):
            self.rec.cwd = p["cwd"]

    def _event(self, p: dict, ts: str) -> None:
        etype = p.get("type")
        rec = self.rec

        if etype == "task_started":
            turn = self.ensure_turn(p.get("turn_id") or "")
            turn.started_at = turn.started_at or ts
            cw = p.get("model_context_window")
            if isinstance(cw, int) and cw:
                rec.context_window = cw
        elif etype == "task_complete":
            turn = self.ensure_turn(p.get("turn_id") or "")
            turn.ended_at = p.get("completed_at") or ts
            turn.duration_ms = p.get("duration_ms")
            turn.time_to_first_token_ms = p.get("time_to_first_token_ms")
            last = (p.get("last_agent_message") or "").strip()
            if last:
                self.add_message("assistant", last, ts)
        elif etype == "user_message":
            self.add_message("user", p.get("message") or "", ts)
        elif etype == "agent_message":
            phase = p.get("phase") or "final"
            role = "commentary" if phase == "commentary" else "assistant"
            self.add_message(role, p.get("message") or "", ts)
        elif etype == "token_count":
            usage = _extract_usage(p.get("info"))
            if usage:
                turn = self.ensure_turn()
                turn.token_usage = usage
                rec.total_tokens = usage
            rl = p.get("rate_limits") or {}
            primary = rl.get("primary") or {}
            if primary:
                rec.rate_limit_note = (
                    f"{rl.get('plan_type', '?')} plan - {primary.get('used_percent', '?')}% of "
                    f"{primary.get('window_minutes', '?')}m window used"
                    + (f", resets at {primary.get('resets_at')}" if primary.get("resets_at") else "")
                )
        elif etype == "error":
            self.add_error(p.get("message") or _pretty(p.get("codex_error_info")), ts)
        elif etype == "mcp_tool_call_end":
            call = self.call_or_create(p.get("call_id") or "", p.get("tool_name") or "mcp_tool", "mcp", "mcp")
            call.output = _clip(_pretty(p.get("result")), MAX_STORED_OUTPUT)
        elif etype == "exec_command_end":
            call = self.call_or_create(p.get("call_id") or "", "shell_command", "shell")
            call.arguments = _pretty({"command": p.get("command"), "cwd": p.get("cwd")})
            out = p.get("aggregated_output")
            if not out:
                out = (p.get("stdout") or "") + ((p.get("stderr") or "") and "\n[stderr]\n" + (p.get("stderr") or ""))
            call.output = _clip(out or "", MAX_STORED_OUTPUT)
        elif etype == "patch_apply_end":
            call = self.call_or_create(p.get("call_id") or "", "apply_patch", "patch", "custom")
            call.arguments = _pretty({"file_path": p.get("file_path")})
            call.success = bool(p.get("success"))
            err = p.get("error")
            call.output = _clip(("" if call.success else f"error: {err}\n") + (p.get("output") or ""), MAX_STORED_OUTPUT)
        elif etype == "dynamic_tool_call_request":
            tool = p.get("tool")
            name = tool.get("name") if isinstance(tool, dict) else tool
            call = self.call_or_create(p.get("callId") or p.get("call_id") or "", name or "dynamic_tool", "dynamic")
            call.arguments = _pretty(p.get("arguments"))
        elif etype == "dynamic_tool_call_response":
            call = self.find_call(p.get("callId") or p.get("call_id") or "")
            if call is None:
                call = self.add_call(ToolCall(name="dynamic_tool", kind="dynamic",
                                              call_id=p.get("callId") or p.get("call_id") or ""))
            call.output = _clip(_pretty(p.get("result")), MAX_STORED_OUTPUT)
        elif etype == "view_image_tool_call":
            call = self.call_or_create(p.get("call_id") or "", "view_image", "view_image")
            call.arguments = _pretty({"path": p.get("path")})

    def _response(self, p: dict, ts: str) -> None:
        """Handle v2 response_item lines."""
        rtype = p.get("type")
        if rtype == "message":
            role = p.get("role") or "assistant"
            if role == "developer":
                role = "developer"
            self.add_message(role, _content_text(p.get("content")), ts)
        elif rtype == "agent_message":
            # Inter-agent message (from parent to subagent or back)
            content = p.get("content", [])
            author = p.get("author", "")
            recipient = p.get("recipient", "")
            for c in content:
                if isinstance(c, dict) and c.get("type") == "input_text":
                    self.add_message("agent", c["text"], ts, author=author, recipient=recipient)
        elif rtype == "reasoning":
            summary = p.get("summary") or []
            texts = []
            for block in summary:
                if isinstance(block, dict) and block.get("text"):
                    texts.append(str(block["text"]))
                elif isinstance(block, str):
                    texts.append(block)
            self.add_reasoning("\n".join(texts))
        elif rtype == "function_call":
            call = self.call_or_create(
                p.get("call_id") or "", p.get("name") or "function_call", "function", p.get("namespace") or "-"
            )
            call.arguments = _pretty(p.get("arguments"))
        elif rtype == "function_call_output":
            call = self.find_call(p.get("call_id") or "")
            if call is None:
                call = self.add_call(ToolCall(name="(unknown)", kind="function", call_id=p.get("call_id") or ""))
            call.output = _clip(_pretty(p.get("output")), MAX_STORED_OUTPUT)
        elif rtype == "custom_tool_call":
            call = self.call_or_create(p.get("call_id") or "", p.get("name") or "custom_tool", "custom", "custom")
            call.arguments = _clip(p.get("input") if isinstance(p.get("input"), str) else _pretty(p.get("input")),
                                   MAX_STORED_OUTPUT)
        elif rtype == "custom_tool_call_output":
            call = self.find_call(p.get("call_id") or "")
            if call is None:
                call = self.add_call(ToolCall(name="(unknown)", kind="custom", call_id=p.get("call_id") or ""))
            call.output = _clip(_pretty(p.get("output")), MAX_STORED_OUTPUT)

    def _inter_agent(self, p: dict, ts: str) -> None:
        """Handle inter_agent_communication_metadata lines."""
        # These are metadata wrappers around agent_message response_items
        # The actual content is in the adjacent response_item
        pass

    def _token_usage_record(self, p: dict, ts: str) -> None:
        """Handle explicit token_usage_record lines (v2)."""
        usage = _extract_usage(p.get("usage", p))
        if usage:
            turn = self.ensure_turn()
            turn.token_usage = usage
            self.rec.total_tokens = usage


def parse_transcript(
    path: Path,
    session_id: str = "",
    on_progress: Optional[Callable[[float], None]] = None,
    cancel: Optional[Callable[[], bool]] = None,
) -> SessionRecord:
    """Stream-parse one rollout JSONL file into a SessionRecord."""
    path = Path(path)
    rec = SessionRecord(session_id=session_id or path.stem.rsplit("-", 1)[-1])
    if not path.is_file():
        rec.transcript_missing = True
        rec.warnings.append("transcript file missing on disk")
        return rec

    rec.transcript_path = path
    rec.size_bytes = path.stat().st_size
    parser = _TranscriptParser(rec)

    size = max(rec.size_bytes, 1)
    read_bytes = 0
    last_report = 0.0

    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for lineno, line in enumerate(fh, 1):
            if cancel is not None and lineno % 250 == 0 and cancel():
                rec.warnings.append("parse cancelled by user")
                return rec
            read_bytes += len(line)
            if on_progress is not None and read_bytes - last_report >= PROGRESS_BYTES:
                last_report = read_bytes
                on_progress(min(read_bytes / size, 1.0))
            line = line.strip()
            if not line:
                continue
            rec.line_count += 1
            try:
                obj = json.loads(line)
            except Exception:
                rec.corrupted_lines += 1
                rec.warnings.append(f"line {lineno}: corrupt JSON skipped")
                continue
            if not isinstance(obj, dict):
                rec.corrupted_lines += 1
                continue
            parser.handle(obj, obj.get("timestamp") or "")

    if on_progress is not None:
        on_progress(1.0)
    if rec.corrupted_lines:
        rec.warnings.append(f"{rec.corrupted_lines} corrupt line(s) skipped")
    if not rec.turns:
        rec.warnings.append("empty session (metadata only)")
    return rec
