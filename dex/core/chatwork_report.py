"""Markdown renderer for the Chat & Work archaeology report."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from .chatwork import ChatWorkReport, ChatThread, ExportArtifact
from .redaction import Redactor


def _stamp() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")


def _clip(text: str, limit: int = 70) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |",
           "|" + "|".join("---" for _ in header) + "|"]
    for row in rows:
        cells = [str(c).replace("|", "\\|").replace("\n", " ") for c in row]
        out.append("| " + " | ".join(cells) + " |")
    return out


def _origin_badge(t: ChatThread) -> str:
    if t.conversation_origin == "tpp":
        return "💬 ChatGPT-typed"
    if t.source_kind == "chatgpt":
        return "🌐 chatgpt.com"
    if t.source_kind == "vscode":
        return "🛠 VS Code"
    return t.source_kind or "-"


def build_chatwork_report(report: ChatWorkReport, redactor: Redactor,
                          version: str = "", include_exports: bool = True) -> str:
    """Render the Chat & Work archaeology report as Markdown."""
    red = redactor
    L: list[str] = []
    stats = report.stats()

    L += ["# ChatGPT Chat & Work — Local Archaeology Report", ""]
    L += [f"**Generated**: {_stamp()}"]
    L += [f"**Tool**: DeKodX {version}"]
    L += [f"**Codex source**: `{red.path(str(report.source_codex))}`"]
    L += [f"**Redaction level**: `{red.level}`"]
    L += [""]
    L += ["> This report inventories what your **chatgpt.com** (non-Codex) account "
          "leaves on this machine: the Codex Desktop thread catalog bridge, "
          "chatgpt-linked rollouts, browser traces and export archives. "
          "Conversation *bodies* live on OpenAI servers — only what synced or was "
          "downloaded locally is recoverable here.", ""]
    L += ["---", ""]

    # -- headline stats ------------------------------------------------------ #
    L += ["## Headlines", ""]
    L += [f"- **chatgpt.com threads in local catalog**: {stats['catalog_threads']} "
          f"({stats['tpp_threads']} typed directly into ChatGPT)"]
    L += [f"- **Threads with a local rollout transcript**: {stats['rollout_linked']}"
          + (f" (+{stats['orphan_rollouts']} orphans not in catalog)" if stats['orphan_rollouts'] else "")]
    L += [f"- **Browser profiles with chatgpt.com data**: {stats['browsers']} "
          f"({stats['browser_visits']} history rows, {stats['indexeddb_kb']:,} KB IndexedDB)"]
    L += [f"- **Export archives triaged**: {stats['exports']}"
          + (f" ({stats['export_pii_files']} contain PII markers)" if stats['export_pii_files'] else "")]
    L += ["", "---", ""]

    # -- how each artifact is marked ------------------------------------------ #
    L += ["## How ChatGPT marks data locally", ""]
    L += ["| Artifact | Where | What it holds | Recoverable body? |",
          "|---|---|---|---|"]
    L += ["| Thread catalog | `~/.codex/sqlite/codex-dev.db` → `local_thread_catalog` "
          "(rows with `source_kind='chatgpt'`, `host_id='chatgpt:…'`) | thread id, title, "
          "created/updated, `conversation_origin='tpp'` for chats typed into ChatGPT | "
          "No — index only |"]
    L += ["| Sync state | same DB → `local_thread_catalog_sync_state` | watermark, "
          "observation_sequence per host | — |"]
    L += ["| Global-state atoms | `~/.codex/.codex-global-state.json` → "
          "`client-thread-bindings-v1.*`, `chatgpt-sidebar-state-v1.*` (Projects), "
          "`chatgpt-conversation-resume-tokens-v1`, `chatgpt-last-selected-model-v1` | "
          "local↔chatgpt thread map, Project list, resume-token thread ids, last model | — |"]
    L += ["| Agent rollouts | `~/.codex/sessions/**.jsonl` where `session_meta.source='chatgpt'` "
          "or carries `chatgpt_conversation_id` | full turns, tool calls, tokens | **Yes** |"]
    L += ["| Browser history | `<browser>/User Data/<profile>/History` | "
          "`chatgpt.com/c/<thread-id>` visits, titles, counts | No — visit metadata |"]
    L += ["| IndexedDB | `<profile>/IndexedDB/https_chatgpt.com_0.indexeddb.*` | "
          "app cache blobs (snappy-leveldb) | No — compressed binary |"]
    L += ["| Exports | wherever you saved them (`.zip`/`.json`/`.md`) | sandbox bundles, "
          "shared-chat exports, data-export archives | **Yes** |"]
    L += ["", "---", ""]

    # -- thread index ---------------------------------------------------------- #
    L += ["## Thread Catalog", ""]
    L += [f"_{len(report.catalog_threads)} chatgpt.com conversations indexed locally "
          "(newest first). `💬` = typed into ChatGPT (tpp); `🌐` = chatgpt.com origin; "
          "`🛠` = VS Code bridge._", ""]
    rows = []
    for t in sorted(report.catalog_threads, key=lambda x: x.updated_at, reverse=True):
        icon = "💬" if t.conversation_origin == "tpp" else ("🌐" if t.source_kind == "chatgpt" else "🛠")
        rows.append([
            f"`{red.session_id(t.thread_id)}`",
            f"{icon} {_clip(red.apply(t.title))}",
            (t.updated_at or t.created_at or "-")[:16].replace("T", " "),
            "✓" if not t.catalog_only else "—",
            str(t.rollout_turns) if t.local_rollout else "-",
            str(t.browser_visits) if t.browser_visits else "-",
        ])
    if rows:
        L += _table(["Thread ID", "Title", "Last active", "Rollout", "Turns", "Visits"], rows)
    else:
        L += ["_(no chatgpt threads in catalog — is Codex Desktop installed and signed in?)_"]
    L += ["", "---", ""]

    # -- linked rollouts --------------------------------------------------------- #
    linked = [t for t in report.all_threads if not t.catalog_only]
    if linked:
        L += ["## Conversations Recoverable Locally (rollouts)", ""]
        L += ["_These chats have full local transcripts because the agent ran through "
              "Codex while surfaced in chatgpt.com._", ""]
        rows = []
        for t in linked:
            rows.append([
                f"`{red.session_id(t.thread_id)}`",
                _clip(red.apply(t.title)),
                red.path(t.local_rollout).replace("\\", "/").split("/")[-1][:40],
                str(t.rollout_turns), str(t.rollout_messages), str(t.rollout_tools),
            ])
        L += _table(["Thread", "Title", "Rollout file", "Turns", "Msgs", "Tools"], rows)
        L += ["", "_Use DeKodX's main Recover tab to extract the full conversation "
              "from these rollouts (they are regular Codex sessions)._"]
        L += ["", "---", ""]

    # -- browser traces ------------------------------------------------------------ #
    L += ["## Browser Traces", ""]
    if report.browsers:
        rows = []
        for b in report.browsers:
            rows.append([
                f"{b.browser} / {b.profile}",
                str(b.history_hits),
                (b.last_visit or "-")[:16].replace("T", " "),
                f"{b.indexeddb_bytes // 1024:,} KB" if b.indexeddb_bytes else "-",
                str(b.service_worker_caches) if b.service_worker_caches else "-",
            ])
        L += _table(["Profile", "chatgpt.com history rows", "Last visit", "IndexedDB", "SW caches"], rows)
        L += [""]
        L += ["_IndexedDB and Local Storage hold chatgpt.com's offline cache in "
              "snappy-compressed leveldb — titles and fragments may exist there, but "
              "they are not plaintext-recoverable without the browser. History rows "
              "confirm which conversations you opened and when._"]
    else:
        L += ["_(no chatgpt.com browser data found in Chromium profiles)_"]
    L += ["", "---", ""]

    # -- exports ---------------------------------------------------------------------- #
    if include_exports and report.exports:
        L += ["## Export Archives", ""]
        for art in report.exports:
            _export_block(art, L, red)
        L += ["", "---", ""]

    # -- hosts & sync ------------------------------------------------------------------- #
    if report.global_state and (report.global_state.thread_bindings
                                or report.global_state.projects
                                or report.global_state.resume_token_threads):
        gs = report.global_state
        L += ["## ChatGPT Atoms (global state)", ""]
        L += ["_Extra chatgpt.com markers inside `.codex-global-state.json`._", ""]
        if gs.last_model:
            L += [f"- **Last selected ChatGPT model**: `{gs.last_model}`"]
        if gs.projects:
            L += [f"- **Projects ({len(gs.projects)})**:"]
            for p in gs.projects:
                L += [f"  - `{p['id'][:16]}…` **{red.apply(p['name'] or '(unnamed)')}**"
                      + (f" — created {p['created'][:10]}" if p['created'] else "")]
        if gs.thread_bindings:
            L += [f"- **Client thread bindings ({len(gs.thread_bindings)})**: "
                  "local Codex thread → chatgpt.com thread"]
            for local, remote in list(gs.thread_bindings.items())[:15]:
                L += [f"  - `{red.session_id(local)}` → `{red.session_id(remote)}`"]
            if len(gs.thread_bindings) > 15:
                L += [f"  - … {len(gs.thread_bindings) - 15} more"]
        if gs.resume_token_threads:
            refs = ", ".join(f"`{red.session_id(t)}`" for t in gs.resume_token_threads[:10])
            L += [f"- **Resume tokens held for**: {refs}"
                  + (" …" if len(gs.resume_token_threads) > 10 else "")]
        L += ["", "---", ""]

    if report.hosts:
        L += ["## Catalog Hosts", ""]
        rows = [[_clip(red.apply(h.get("host_id", "?")), 60), str(h.get("host_kind", "-"))]
                for h in report.hosts]
        L += _table(["Host", "Kind"], rows)
        L += [""]
    if report.sync_state:
        L += ["## Sync State", ""]
        rows = []
        for s in report.sync_state:
            rows.append([
                _clip(red.apply(s.get("host_id", "?")), 50),
                _stamp_of(s.get("watermark_updated_at")),
                str(s.get("observation_sequence", "-")),
                "yes" if s.get("initial_build_complete") else "no",
            ])
        L += _table(["Host", "Watermark", "Observations", "Initial build"], rows)
        L += ["", "---", ""]

    # -- warnings ------------------------------------------------------------------------ #
    L += ["## Warnings", ""]
    if report.warnings:
        for w in report.warnings[:100]:
            L += [f"- {red.apply(w)}"]
    else:
        L += ["_(none)_"]
    L += ["", "---", ""]
    L += ["_Report generated locally by DeKodX — no data left this machine. "
          "chatgpt.com conversation bodies remain on OpenAI servers; this report "
          "covers the local footprint only._", ""]
    return "\n".join(L).rstrip() + "\n"


def _stamp_of(value) -> str:
    from .chatwork import _epoch_to_iso
    iso = _epoch_to_iso(value)
    return iso[:16].replace("T", " ") if iso else "-"


def _export_block(art: ExportArtifact, L: list[str], red: Redactor) -> None:
    L += [f"### `{art.path.name}`", ""]
    L += [f"- **Path**: `{red.path(str(art.path))}`"]
    L += [f"- **Kind**: {art.kind} · **Size**: {art.size_bytes:,} B"]
    if art.sha256:
        L += [f"- **SHA-256**: `{art.sha256[:16]}…`"]
    if art.error:
        L += [f"- **Error**: {art.error}"]
    if art.sandbox_ids:
        L += [f"- **Sandbox ids**: {', '.join('`' + s + '`' for s in art.sandbox_ids)}"]
    if art.pii_hits:
        hits = ", ".join(f"{k}×{v}" for k, v in sorted(art.pii_hits.items()))
        L += [f"- **⚠ PII markers inside**: {hits} "
              "(values not extracted — counts only)"]
    if art.chat_thread_refs:
        refs = ", ".join(f"`{red.session_id(r)}`" for r in art.chat_thread_refs[:10])
        L += [f"- **Thread-id references**: {refs}"]
    if art.entries:
        shown = art.entries[:12]
        L += ["- **Entries**:"]
        for e in shown:
            L += [f"  - `{red.apply(_clip(e, 90))}`"]
        if len(art.entries) > len(shown):
            L += [f"  - … {len(art.entries) - len(shown)} more"]
    L += [""]
