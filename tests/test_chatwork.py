"""End-to-end tests for the Chat & Work (chatgpt.com non-Codex) engine.

Run:  python tests/test_chatwork.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import make_chatwork_fixture as fx  # noqa: E402

from dex.core.chatwork import (  # noqa: E402
    run_chatwork, scan_export, read_catalog,
)
from dex.core.chatwork_report import build_chatwork_report  # noqa: E402
from dex.core.redaction import Redactor  # noqa: E402

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
    tmp = Path(tempfile.mkdtemp(prefix="dekodx_cw_"))
    paths = fx.build_all(tmp)

    # -- catalog ------------------------------------------------------------- #
    print("\n-- catalog --")
    threads, hosts, sync, warnings = read_catalog(paths["codex"])
    check("3 catalog rows", len(threads) == 3)
    check("tpp thread detected", any(t.conversation_origin == "tpp" for t in threads))
    check("tpp is Navier thread",
          any(t.conversation_origin == "tpp" and t.title == "Navier Stokes Audit Plan"
              for t in threads))
    check("hosts read", len(hosts) == 2)
    check("sync state read", len(sync) == 1)
    check("no warnings on valid fixture", not warnings)

    # -- rollout linking ------------------------------------------------------ #
    print("\n-- rollout linking --")
    report = run_chatwork(
        paths["codex"], [paths["export"]],
        tmp_dir=tmp / "scan",
        scan_browser=False,          # browser part tested separately below
        log=lambda m, l='info': None, progress=lambda f, s='': None,
    )
    tpp = next(t for t in report.catalog_threads if t.thread_id == fx.T_TPP)
    check("tpp rollout linked", not tpp.catalog_only)
    check("rollout turns counted", tpp.rollout_turns == 3)
    check("rollout messages counted", tpp.rollout_messages == 6)
    check("orphan rollout found", any(t.thread_id == fx.T_ORPHAN
                                      for t in report.rollout_threads))
    check("vscode rollout NOT linked as chatgpt",
          all(t.thread_id != "6aa11111-2222-4333-8444-555555555555"
              for t in report.all_threads))
    chat = next(t for t in report.catalog_threads if t.thread_id == fx.T_CHAT)
    check("catalog-only thread stays catalog-only", chat.catalog_only)

    # -- export triage --------------------------------------------------------- #
    print("\n-- export triage --")
    art = scan_export(paths["export"])
    check("zip kind", art.kind == "zip")
    check("zip entries", len(art.entries) == 4)
    check("email PII counted", art.pii_hits.get("email", 0) >= 1)
    check("api-key PII counted", art.pii_hits.get("api-key", 0) >= 1)
    check("sandbox id extracted", "e8adf3fa411b" in art.sandbox_ids)
    check("thread ref extracted", fx.T_TPP in art.chat_thread_refs)
    check("sha256 computed", len(art.sha256) == 64)

    # -- browser scan (monkeypatch LOCALAPPDATA to the fixture) ----------------- #
    print("\n-- browser scan --")
    os.environ["LOCALAPPDATA"] = str(tmp / "AppData" / "Local")
    # re-import to pick up env
    import importlib
    import dex.core.chatwork as cw
    importlib.reload(cw)
    rep2 = cw.run_chatwork(
        paths["codex"], [],
        tmp_dir=tmp / "scan2",
        scan_rollout_bodies=False,
        log=lambda m, l='info': None, progress=lambda f, s='': None,
    )
    check("brave profile found", any(b.browser == "Brave" for b in rep2.browsers))
    brave = next((b for b in rep2.browsers if b.browser == "Brave"), None)
    check("history rows counted", brave is not None and brave.history_hits == 3)
    check("indexeddb bytes counted", brave is not None and brave.indexeddb_bytes >= 1024)
    tpp2 = next(t for t in rep2.catalog_threads if t.thread_id == fx.T_TPP)
    check("tpp browser visits linked", tpp2.browser_visits == 3)
    check("last visit stamped", bool(tpp2.last_browser_visit))

    # -- global state atoms ------------------------------------------------------ #
    print("\n-- global state atoms --")
    from dex.core.chatwork import read_global_state
    gstate, gs_warn = read_global_state(paths["codex"])
    check("thread binding parsed", gstate.thread_bindings.get(
        "local-chatgpt:a07e2b6c-1d99-423a-8869-078de496f033") == fx.T_CHAT)
    check("projects parsed", len(gstate.projects) == 2)
    check("project name kept", any(p["name"] == "Editing Toolcall" for p in gstate.projects))
    check("resume token thread parsed", fx.T_TPP in gstate.resume_token_threads)
    check("last model parsed", gstate.last_model == "gpt-5-6-thinking")
    md_gs = build_chatwork_report(
        report, Redactor(level="none", home_dirs=[]), version="t")
    check("report has atoms section", "## ChatGPT Atoms" in md_gs)
    check("report shows project", "Editing Toolcall" in md_gs)

    # -- report rendering --------------------------------------------------------- #
    print("\n-- report --")
    red = Redactor(level="standard", home_dirs=[tmp / "home" / "Alice"])
    md = build_chatwork_report(rep2, red, version="test")
    check("title present", md.startswith("# ChatGPT Chat & Work"))
    check("headlines section", "## Headlines" in md)
    check("how-marked table", "local_thread_catalog" in md)
    check("thread catalog table", "## Thread Catalog" in md)
    check("tpp badge", "💬" in md)
    check("navier title kept", "Navier Stokes Audit Plan" in md)
    check("browser section", "## Browser Traces" in md)
    check("thread ids trimmed at standard", fx.T_TPP[:8] in md and fx.T_TPP not in md)
    check("warnings section", "## Warnings" in md)

    md_paranoid = build_chatwork_report(
        rep2, Redactor(level="paranoid", home_dirs=[tmp / "home" / "Alice"]), version="t")
    check("paranoid trims ids harder", fx.T_TPP[:12] not in md_paranoid)

    md_full = run_and_render(paths, tmp, "none")
    check("none level keeps full ids", fx.T_TPP in md_full)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


def run_and_render(paths: dict, tmp: Path, level: str) -> str:
    rep = run_chatwork(
        paths["codex"], [paths["export"]],
        tmp_dir=tmp / "scan3",
        scan_browser=False,
        log=lambda m, l='info': None, progress=lambda f, s='': None,
    )
    return build_chatwork_report(
        rep, Redactor(level=level, home_dirs=[tmp / "home" / "Alice"]), version="t")


if __name__ == "__main__":
    raise SystemExit(main())
