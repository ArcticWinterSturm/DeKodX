"""Analytics event models for Codex Desktop capture.

These correspond to the TrackEventsRequest shapes documented in codex-rs/analytics/src/.
When CODEX_ANALYTICS_EVENTS_CAPTURE_FILE is set, the app-server appends
one JSON object per line — the outbound analytics upload, captured locally.

Env vars:
  CODEX_ANALYTICS_EVENTS_CAPTURE_FILE = path to JSONL capture file (0600, append)
  CODEX_ROLLOUT_TRACE_ROOT = directory for per-request trace bundles

Reference: chatgpt-desktop-codex-research.md §4, §4b, §9
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TurnResolvedConfigFact:
    turn_id: str = ""
    thread_id: str = ""
    num_input_images: int = 0
    submission_type: str = ""
    ephemeral: bool = False
    session_source: str = ""
    model: str = ""
    model_provider: str = ""
    permission_profile: str = ""
    permission_profile_cwd: str = ""
    reasoning_effort: str = ""
    reasoning_summary: str = ""
    service_tier: str = ""
    approval_policy: str = ""
    approvals_reviewer: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "TurnResolvedConfigFact":
        return cls(
            turn_id=str(d.get("turn_id", "")),
            thread_id=str(d.get("thread_id", "")),
            num_input_images=int(d.get("num_input_images", 0)),
            submission_type=str(d.get("submission_type", "")),
            ephemeral=bool(d.get("ephemeral", False)),
            session_source=str(d.get("session_source", "")),
            model=str(d.get("model", "")),
            model_provider=str(d.get("model_provider", "")),
            permission_profile=str(d.get("permission_profile", "")),
            permission_profile_cwd=str(d.get("permission_profile_cwd", "")),
            reasoning_effort=str(d.get("reasoning_effort", "")),
            reasoning_summary=str(d.get("reasoning_summary", "")),
            service_tier=str(d.get("service_tier", "")),
            approval_policy=str(d.get("approval_policy", "")),
            approvals_reviewer=str(d.get("approvals_reviewer", "")),
        )


@dataclass
class TurnTokenUsageFact:
    turn_id: str = ""
    thread_id: str = ""
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    total_tokens: int = 0

    @classmethod
    def from_dict(cls, d: dict) -> "TurnTokenUsageFact":
        tu = d.get("token_usage", {}) or {}
        return cls(
            turn_id=str(d.get("turn_id", "")),
            thread_id=str(d.get("thread_id", "")),
            input_tokens=int(tu.get("input_tokens", 0)),
            cached_input_tokens=int(tu.get("cached_input_tokens", 0)),
            cache_write_input_tokens=int(tu.get("cache_write_input_tokens", 0)),
            output_tokens=int(tu.get("output_tokens", 0)),
            reasoning_output_tokens=int(tu.get("reasoning_output_tokens", 0)),
            total_tokens=int(tu.get("total_tokens", 0)),
        )


@dataclass
class TurnProfile:
    """Per-turn time decomposition — the one server-timing-adjacent measurement you DO have."""
    turn_id: str = ""
    thread_id: str = ""
    before_first_sampling_ms: int = 0
    sampling_ms: int = 0
    compaction_ms: int = 0
    between_sampling_overhead_ms: int = 0
    tool_blocking_ms: int = 0
    after_last_sampling_ms: int = 0
    sampling_request_count: int = 0
    sampling_retry_count: int = 0

    @property
    def total_model_ms(self) -> int:
        return self.sampling_ms

    @property
    def total_tool_ms(self) -> int:
        return self.tool_blocking_ms

    @property
    def total_overhead_ms(self) -> int:
        return self.before_first_sampling_ms + self.between_sampling_overhead_ms + self.after_last_sampling_ms + self.compaction_ms

    @property
    def total_wall_ms(self) -> int:
        return self.total_model_ms + self.total_tool_ms + self.total_overhead_ms

    @classmethod
    def from_dict(cls, d: dict) -> "TurnProfile":
        return cls(
            turn_id=str(d.get("turn_id", "")),
            thread_id=str(d.get("thread_id", "")),
            before_first_sampling_ms=int(d.get("before_first_sampling_ms", 0)),
            sampling_ms=int(d.get("sampling_ms", 0)),
            compaction_ms=int(d.get("compaction_ms", 0)),
            between_sampling_overhead_ms=int(d.get("between_sampling_overhead_ms", 0)),
            tool_blocking_ms=int(d.get("tool_blocking_ms", 0)),
            after_last_sampling_ms=int(d.get("after_last_sampling_ms", 0)),
            sampling_request_count=int(d.get("sampling_request_count", 0)),
            sampling_retry_count=int(d.get("sampling_retry_count", 0)),
        )


@dataclass
class GuardianReviewEventParams:
    """Guardian/review thread analytics — the only place decision + outcome + risk_level coexist."""
    thread_id: str = ""
    turn_id: str = ""
    review_id: str = ""
    target_item_id: str = ""
    approval_request_source: str = ""
    reviewed_action: str = ""
    reviewed_action_truncated: bool = False
    decision: str = ""
    terminal_status: str = ""
    failure_reason: str = ""
    attempt_count: int = 0
    risk_level: str = ""
    user_authorization: str = ""
    outcome: str = ""
    guardian_thread_id: str = ""
    guardian_session_kind: str = ""
    guardian_model: str = ""
    guardian_reasoning_effort: str = ""
    guardian_default_review_model_id: str = ""
    guardian_catalog_contains_auto_review: bool = False
    guardian_review_model_overridden: bool = False
    guardian_model_provider_id: str = ""
    had_prior_review_context: bool = False
    review_timeout_ms: int = 0
    tool_call_count: int = 0
    time_to_first_token_ms: int = 0
    completion_latency_ms: int = 0
    started_at: str = ""
    completed_at: str = ""
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    total_tokens: int = 0

    @classmethod
    def from_dict(cls, d: dict) -> "GuardianReviewEventParams":
        def s(k, default=""):
            return str(d.get(k, default)) if d.get(k) is not None else default

        def i(k, default=0):
            return int(d.get(k, default)) if d.get(k) is not None else default

        def b(k, default=False):
            return bool(d.get(k, default))

        return cls(
            thread_id=s("thread_id"), turn_id=s("turn_id"), review_id=s("review_id"),
            target_item_id=s("target_item_id"), approval_request_source=s("approval_request_source"),
            reviewed_action=s("reviewed_action"), reviewed_action_truncated=b("reviewed_action_truncated"),
            decision=s("decision"), terminal_status=s("terminal_status"), failure_reason=s("failure_reason"),
            attempt_count=i("attempt_count"), risk_level=s("risk_level"), user_authorization=s("user_authorization"),
            outcome=s("outcome"), guardian_thread_id=s("guardian_thread_id"),
            guardian_session_kind=s("guardian_session_kind"), guardian_model=s("guardian_model"),
            guardian_reasoning_effort=s("guardian_reasoning_effort"),
            guardian_default_review_model_id=s("guardian_default_review_model_id"),
            guardian_catalog_contains_auto_review=b("guardian_catalog_contains_auto_review"),
            guardian_review_model_overridden=b("guardian_review_model_overridden"),
            guardian_model_provider_id=s("guardian_model_provider_id"),
            had_prior_review_context=b("had_prior_review_context"),
            review_timeout_ms=i("review_timeout_ms"), tool_call_count=i("tool_call_count"),
            time_to_first_token_ms=i("time_to_first_token_ms"),
            completion_latency_ms=i("completion_latency_ms"),
            started_at=s("started_at"), completed_at=s("completed_at"),
            input_tokens=i("input_tokens"), cached_input_tokens=i("cached_input_tokens"),
            cache_write_input_tokens=i("cache_write_input_tokens"),
            output_tokens=i("output_tokens"), reasoning_output_tokens=i("reasoning_output_tokens"),
            total_tokens=i("total_tokens"),
        )


@dataclass
class SubAgentThreadStartedInput:
    session_id: str = ""
    thread_id: str = ""
    parent_thread_id: str = ""
    forked_from_thread_id: str = ""
    product_client_id: str = ""
    client_name: str = ""
    client_version: str = ""
    model: str = ""
    ephemeral: bool = False
    thread_source: str = ""
    subagent_source: str = ""
    created_at: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "SubAgentThreadStartedInput":
        return cls(
            session_id=str(d.get("session_id", "")),
            thread_id=str(d.get("thread_id", "")),
            parent_thread_id=str(d.get("parent_thread_id", "")),
            forked_from_thread_id=str(d.get("forked_from_thread_id", "")),
            product_client_id=str(d.get("product_client_id", "")),
            client_name=str(d.get("client_name", "")),
            client_version=str(d.get("client_version", "")),
            model=str(d.get("model", "")),
            ephemeral=bool(d.get("ephemeral", False)),
            thread_source=str(d.get("thread_source", "")),
            subagent_source=str(d.get("subagent_source", "")),
            created_at=str(d.get("created_at", "")),
        )


@dataclass
class CodexGoalEvent:
    thread_id: str = ""
    turn_id: str = ""
    goal_id: str = ""
    event_kind: str = ""
    goal_status: str = ""
    has_token_budget: bool = False
    cumulative_tokens_accounted: int = 0

    @classmethod
    def from_dict(cls, d: dict) -> "CodexGoalEvent":
        return cls(
            thread_id=str(d.get("thread_id", "")),
            turn_id=str(d.get("turn_id", "")),
            goal_id=str(d.get("goal_id", "")),
            event_kind=str(d.get("event_kind", "")),
            goal_status=str(d.get("goal_status", "")),
            has_token_budget=bool(d.get("has_token_budget", False)),
            cumulative_tokens_accounted=int(d.get("cumulative_tokens_accounted", 0)),
        )


# --------------------------------------------------------------------------- #
# TrackEventsRequest — the top-level envelope
# --------------------------------------------------------------------------- #

ANALYTICS_FACT_TYPES = {
    "TurnResolvedConfigFact": TurnResolvedConfigFact,
    "TurnTokenUsageFact": TurnTokenUsageFact,
    "TurnProfile": TurnProfile,
    "GuardianReviewEventParams": GuardianReviewEventParams,
    "SubAgentThreadStartedInput": SubAgentThreadStartedInput,
    "CodexGoalEvent": CodexGoalEvent,
}


@dataclass
class TrackEventsRequest:
    """One line from the analytics capture JSONL file."""
    event_type: str = ""
    timestamp: str = ""
    facts: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "TrackEventsRequest":
        event_type = str(d.get("event_type", ""))
        timestamp = str(d.get("timestamp", ""))
        raw_facts = d.get("facts", []) or []
        facts = []
        for rf in raw_facts:
            if isinstance(rf, dict):
                fact_type = rf.get("type", "") or rf.get("fact_type", "")
                model_cls = ANALYTICS_FACT_TYPES.get(fact_type)
                if model_cls:
                    try:
                        facts.append(model_cls.from_dict(rf.get("data", rf)))
                    except Exception:
                        pass
        return cls(event_type=event_type, timestamp=timestamp, facts=facts)
