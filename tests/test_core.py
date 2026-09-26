"""End-to-end tests for the DeKodX core engine (no Qt required).

Run:  python3 tests/test_core.py
Builds a mock .codex fixture in a temp dir, runs every redaction level and
asserts the redaction matrix, edge-case handling and report structure.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import make_fixture  # noqa: E402

from dex.core.models import RecoveryOptions  # noqa: E402
from dex.core.recovery import run_recovery  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, condition: bool) -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="dekodx_test_"))
    source = make_fixture.build(tmp / "home" / "Alice" / ".codex", big=True)
    print(f"fixture: {source}")

    outputs = {}
    for level in ("standard", "paranoid", "none"):
        out = tmp / f"report_{level}.md"
        result = run_recovery(source, out, RecoveryOptions(redaction_level=level))
        outputs[level] = out.read_text(encoding="utf-8")
        check(f"{level}: recovery returned sessions", len(result.sessions) == 5)

    std, par, non = outputs["standard"], outputs["paranoid"], outputs["none"]

    print("\n-- structure --")
    check("header present", std.startswith("# Codex Desktop Session Recovery Report"))
    check("toc present", "## Table of Contents" in std)
    check("session index table", "| Session ID | Title | Last Active | Model | Tokens | Status |" in std)
    check("app state section", "## Application State" in std)
    check("mcp servers table", "### MCP Servers" in std)
    check("statistics section", "## Statistics" in std)
    check("logs section", "## Application Logs" in std)
    check("token usage table", "| Turn | Input | Cached | Output | Reasoning | Total |" in std)
    check("tools used table", "#### Tools Used" in std)
    check("47M tokens stat", "47,231,412" in std)
    check("date range", "2026-04-15 to 2026-07-03" in std)

    print("\n-- edge cases --")
    check("missing transcript flagged", "**Transcript missing**" in std)
    check("empty session flagged", "empty session" in std)
    check("corrupt line counted", "corrupt JSON skipped" in std)
    check("encrypted reasoning marker", "[ENCRYPTED REASONING - UNRECOVERABLE]" in std)
    check("large transcript parsed (16 turns)", std.count("**Turn ") >= 17)
    check("commentary captured", "**Agent Commentary**:" in std)
    check("tool outputs captured", "[Tool Output]:" in std)
    check("duplicate user message deduped",
          std.count("OK use playwright to access") == 1)

    print("\n-- redaction matrix (standard) --")
    for leak in ("Alice", "alice@example.com", "eyJhbGci", "sk-proj", "supersecret",
                 "S-1-5-21", "acct_9f8e", "ghp_Token", "pid:4321", INSTALL := make_fixture.INSTALL_ID):
        check(f"no leak: {leak[:14]}", leak not in std)
    check("home path redacted (win)", "C:\\Users\\[USER]\\" in std)
    check("session ids partial in index", "`019f2960-…`" in std)
    check("full id kept in detail header", "019f2960-aba8-7c70-8601-41bcadccd384" in std)
    check("call ids partial", "call_aBSvuu4" in std and "call_aBSvuu4F0001" not in std)
    check("model names kept", "gpt-5.4-mini" in std)
    check("user prompts kept", "use sequential thinking" in std)

    print("\n-- levels --")
    check("paranoid strips absolute paths", "C:\\editing" not in par and "[PATH]" in par)
    check("paranoid strips urls", "example.com" not in par)
    check("paranoid trims ids harder", "019f2960-aba8" not in par)
    check("none keeps emails", "alice@example.com" in non)
    check("none still hides jwt", "eyJhbGci" not in non)
    check("none still hides api key", "sk-proj" not in non)
    check("none still hides wp pass", "supersecret" not in non)

    print("\n-- cancellation --")
    from dex.core.models import RecoveryCancelled
    calls = {"n": 0}

    def cancel():
        calls["n"] += 1
        return calls["n"] > 3

    try:
        run_recovery(source, tmp / "cancelled.md", RecoveryOptions(), cancel=cancel)
        check("cancel raises", False)
    except RecoveryCancelled:
        check("cancel raises", True)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
