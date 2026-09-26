"""Parsers for config.toml, auth.json and .codex-global-state.json."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

try:                                     # Python 3.11+
    import tomllib as _toml
except ModuleNotFoundError:              # pragma: no cover - fallback
    try:
        import tomli as _toml            # type: ignore
    except ModuleNotFoundError:
        _toml = None


def load_toml(path: Path) -> dict[str, Any]:
    if _toml is None:
        return {}
    try:
        with open(path, "rb") as fh:
            return _toml.load(fh)
    except Exception:
        return {}


def load_json(path: Path) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def read_text(path: Path, limit: int = 4096) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(limit)
    except OSError:
        return ""


# --------------------------------------------------------------------------- #
# config.toml
# --------------------------------------------------------------------------- #

def config_summary(cfg: dict) -> dict:
    mcp = cfg.get("mcp_servers") or {}
    servers = []
    for name, spec in mcp.items():
        spec = spec if isinstance(spec, dict) else {}
        command = " ".join(str(x) for x in (spec.get("command") or [])) or spec.get("url", "")
        env = spec.get("env") or {}
        servers.append(
            {
                "name": name,
                "command": command,
                "enabled": bool(spec.get("enabled", True)),
                "env_keys": sorted(env.keys()),
                "env_secrets": {k: str(v) for k, v in env.items()},
            }
        )

    # Detect new fields
    desktop = cfg.get("desktop", {})
    features = cfg.get("features", {})
    projects = cfg.get("projects", {})

    return {
        "model": cfg.get("model", ""),
        "reasoning_effort": cfg.get("model_reasoning_effort", ""),
        "personality": cfg.get("personality", ""),
        "service_tier": cfg.get("service_tier", ""),
        "mcp_servers": servers,
        "projects": sorted(projects.keys()),
        "project_details": {k: v for k, v in projects.items() if isinstance(v, dict)},
        "marketplaces": sorted((cfg.get("marketplaces") or {}).keys()),
        "plugins": sorted((cfg.get("plugins") or {}).keys()),
        "features": {k: v for k, v in features.items() if isinstance(v, bool)},
        "memories": cfg.get("memories") or {},
        "windows": cfg.get("windows") or {},
        "desktop": desktop,
        "external_agent_import_sync_item_types": cfg.get("external-agent-import-sync-item-types", ""),
        "marketplace_urls": [
            str(m.get("url") or m.get("repo") or "")
            for m in (cfg.get("marketplaces") or {}).values()
            if isinstance(m, dict)
        ],
    }


# --------------------------------------------------------------------------- #
# auth.json
# --------------------------------------------------------------------------- #

AUTH_SECRET_KEYS = ("id_token", "access_token", "refresh_token", "OPENAI_API_KEY", "account_id")


def auth_summary(auth: dict) -> dict:
    tokens = auth.get("tokens") or {}
    present = [k for k in AUTH_SECRET_KEYS if (k in auth and auth[k]) or (k in tokens and tokens[k])]
    return {
        "auth_mode": auth.get("auth_mode", ""),
        "last_refresh": auth.get("last_refresh", ""),
        "present_secrets": present,
    }


def auth_secrets(auth: dict) -> list[str]:
    tokens = auth.get("tokens") or {}
    secrets = []
    for key in AUTH_SECRET_KEYS:
        for bag in (auth, tokens):
            val = bag.get(key)
            if isinstance(val, str) and val.strip():
                secrets.append(val.strip())
    return secrets


# --------------------------------------------------------------------------- #
# .codex-global-state.json
# --------------------------------------------------------------------------- #

def global_state_summary(state: dict) -> dict:
    atom = state.get("electron-persisted-atom-state") or {}
    history = atom.get("prompt-history") or atom.get("promptHistory") or []
    if isinstance(history, dict):
        history = history.get("items") or []
    prompts = [str(h) for h in history if isinstance(h, str)]

    # Also check v2 prompt-history structure
    if not prompts:
        ph = state.get("prompt-history", {})
        if isinstance(ph, dict):
            for v in ph.values():
                if isinstance(v, dict) and v.get("prompt"):
                    prompts.append(v["prompt"])
                elif isinstance(v, str):
                    prompts.append(v)

    roots = []
    for key in ("electron-saved-workspace-roots", "active-workspace-roots"):
        for root in state.get(key) or []:
            if isinstance(root, str) and root not in roots:
                roots.append(root)

    # Detect app version
    app_version = state.get("electron-app-version", "")
    if not app_version:
        # Try to find in plugins
        for key, val in state.items():
            if "codex-app-version" in str(key) and isinstance(val, str):
                app_version = val
                break

    return {
        "workspaces": roots,
        "project_order": [str(p) for p in state.get("project-order") or []],
        "prompt_history": prompts,
        "queued_followups": [str(q) for q in state.get("queued-follow-ups") or []],
        "window_bounds": state.get("electron-main-window-bounds") or {},
        "agent_mode_by_host": sorted((atom.get("agent-mode-by-host-id") or {}).keys()),
        "app_version": app_version,
        "chatgpt_conversation_resume_tokens": bool(state.get("chatgpt-conversation-resume-tokens-v1")),
        "external_agent_import_sync": state.get("external-agent-import-sync-state", {}),
    }


# --------------------------------------------------------------------------- #
# secrets harvested from every config source (always redacted)
# --------------------------------------------------------------------------- #

def collect_config_secrets(cfg: dict, auth: dict, installation_id: str, cap_sid: str) -> list[str]:
    secrets = auth_secrets(auth)
    for blob in (installation_id, cap_sid):
        for line in blob.splitlines():
            line = line.strip()
            if line:
                secrets.append(line)
    summary = config_summary(cfg)
    for server in summary["mcp_servers"]:
        secrets.extend(server["env_secrets"].values())
    # any env-style secret anywhere in the TOML tree
    stack = [cfg]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, str) and any(
                    token in str(key).upper() for token in ("KEY", "TOKEN", "SECRET", "PASS", "SID")
                ):
                    secrets.append(value)
                else:
                    stack.append(value)
        elif isinstance(node, list):
            stack.extend(node)
    seen: set[str] = set()
    return [s for s in secrets if s and len(s) >= 6 and not (s in seen or seen.add(s))]
