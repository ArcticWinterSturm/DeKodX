# DeKodX — Codex Desktop Session Recovery

DeKodX is a local-only forensic recovery utility for [Codex Desktop](https://openai.com/codex)
installations **and chatgpt.com (non-Codex) Chat & Work data**. It reads your `~/.codex` /
`C:\Users\you\.codex` directory and Chromium browser profiles, parses **every** storage
format, reconstructs the full conversation history, inventories your chatgpt.com local
footprint, redacts PII and secrets, and exports **one navigable Markdown report**.

> 🔒 Runs entirely offline. Databases are opened **read-only**. Your Codex install is
> never modified. Hard secrets (auth tokens, API keys, installation id, SIDs, account
> ids) are redacted at *every* redaction level — including "None".



---

## Features

- **Auto-detects** the Codex data directory (`~/.codex`, `$CODEX_HOME`) or browse to a copy
- **Parses all storage formats**
  - `sessions/**/*.jsonl` + `archived_sessions/*.jsonl` rollouts (streamed line-by-line, 35 MB+ safe)
  - `session_index.jsonl` master index
  - `state_5.sqlite` — threads, dynamic tools, goals, jobs, spawn edges, remote-control enrollments
  - `logs_2.sqlite` — Rust tracing logs (verbose targets filtered)
  - `sqlite/codex-dev.db` — inbox items, automations
  - `config.toml`, `auth.json`, `.codex-global-state.json`, `installation_id`, `cap_sid`
- **Reconstructs conversations**: turns, user/developer/assistant messages, agent commentary,
  tool calls (shell / apply_patch / MCP / dynamic / view_image) with arguments *and* outputs,
  encrypted-reasoning markers, errors, per-turn token usage and rate-limit notes
- **Chat & Work archaeology** (chatgpt.com, non-Codex)
  - `sqlite/codex-dev.db` → `local_thread_catalog`: every chatgpt.com thread indexed locally
    (titles, timestamps, `conversation_origin='tpp'` = typed into ChatGPT)
  - `.codex-global-state.json` atoms: `client-thread-bindings-v1` (local↔chatgpt thread map),
    `chatgpt-sidebar-state-v1` (Projects), `chatgpt-conversation-resume-tokens-v1`,
    `chatgpt-last-selected-model-v1`
  - Chromium browser scan (Chrome/Edge/Brave/Vivaldi/Opera): `chatgpt.com/c/<thread-id>`
    history visits linked back to catalog threads, IndexedDB/Service-Worker/Local-Storage footprints
  - Export-archive triage: sandbox download bundles (`.zip`), data exports, shared chats —
    hashed, inventoried, sandbox-id extraction, thread-id cross-references and PII marker counts
- **Redaction matrix** with three levels (Standard / Paranoid / None)
- **Elegant Qt6 dark UI** — sidebar navigation, rich tooltip popups, live progress + log console,
  report preview — plus a dependency-free headless CLI
- **Edge cases handled**: missing transcripts, corrupt JSONL lines, empty sessions, locked/WAL
  databases, binary log payloads, unicode errors, missing optional folders

## Installation (Windows)

1. Copy the `DeKodX` folder anywhere (e.g. `C:\Tools\DeKodX`)
2. Double-click **`install.bat`** — creates `.venv` and installs PyQt6 (one-time, needs internet)
3. Double-click **`launch.bat`** — starts the GUI (use `launch_debug.bat` for a console with tracebacks)

Requirements: Windows 10/11, Python 3.9+ on PATH (3.11+ recommended).

### Linux / macOS

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python run.py                 # GUI
python run.py cli --help      # headless
```

## Using the GUI

| Page            | What it does                                                                                                                                                                                          |
| --------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Dashboard**   | Auto-detects the source, shows session/archive/token/log headlines and a ✓/✗ inventory of every expected Codex file                                                                                   |
| **Recover**     | Source + output paths (LVS-style fields with × and … buttons), content checkboxes, log-row limit, redaction level with live hint, ▶ Start / ✕ Cancel, real-time progress bar and coloured log console |
| **Chat & Work** | chatgpt.com (non-Codex) archaeology: thread catalog, global-state atoms, browser traces, export-archive triage — add exports manually or auto-find them in Desktop/Downloads/Documents                |
| **Report**      | Read-only preview of the written `.md`, plus *Open file*, *Show in folder*, *Copy path*                                                                                                               |
| **Settings**    | Default output dir, auto-open behaviour, live accent-colour switcher, About/privacy notes                                                                                                             |

Every sidebar entry, field, button and section chip carries a rich tooltip popup explaining it.

## Using the CLI (no PyQt6 needed)

```bash
python run.py cli --scan-only                       # inventory only
python run.py cli -s ~/.codex -o report.md          # full recovery
python run.py cli -l paranoid --no-logs --log-limit 250
python run.py cli chatwork -e ~/Desktop/navier-stokes-audit.zip
python run.py cli chatwork --no-browser -l none     # catalog + exports only
```

## Redaction matrix

| Data                                                                | Standard                                | Paranoid              | None                  |
| ------------------------------------------------------------------- | --------------------------------------- | --------------------- | --------------------- |
| auth tokens / API keys / installation id / SIDs / account ids       | `[REDACTED]`                            | `[REDACTED]`          | `[REDACTED]` (always) |
| emails, process UUIDs, git origin URLs, env secrets                 | `[REDACTED]`                            | `[REDACTED]`          | kept                  |
| `C:\Users\You\…`, `/home/you/…`                                     | `…\[USER]\…`                            | `[PATH]`              | kept                  |
| absolute paths & URLs elsewhere                                     | kept                                    | `[PATH]` / `[URL]`    | kept                  |
| session / thread ids                                                | first 8 chars                           | first 4 chars         | full                  |
| tool call ids                                                       | first 12 chars                          | first 6 chars         | full                  |
| encrypted reasoning                                                 | `[ENCRYPTED REASONING - UNRECOVERABLE]` | same                  | same                  |
| prompts, replies, tool names/args, models, timestamps, token counts | kept                                    | kept (paths stripped) | kept                  |

## Project layout

```
DeKodX/
├── run.py                  # entry point (GUI or `cli` sub-command)
├── install.bat             # one-time venv + PyQt6 installer (Windows)
├── launch.bat              # windowed launcher
├── launch_debug.bat        # console launcher
├── requirements.txt
├── dex/
│   ├── cli.py              # headless CLI (recover / audit / capture / chatwork)
│   ├── core/               # pure stdlib engine
│   │   ├── models.py       #   dataclasses (sessions, turns, options)
│   │   ├── scanner.py      #   directory inventory + auto-detect
│   │   ├── transcripts.py  #   streaming JSONL rollout parser
│   │   ├── databases.py    #   read-only SQLite readers (schema-drift safe)
│   │   ├── configs.py      #   TOML/JSON config + secret harvesting
│   │   ├── chatwork.py     #   chatgpt.com (non-Codex) archaeology engine
│   │   ├── chatwork_report.py  #   Chat & Work markdown renderer
│   │   ├── redaction.py    #   the redaction engine (3 levels)
│   │   ├── report.py       #   Markdown report writer
│   │   └── recovery.py     #   orchestrator used by GUI + CLI
│   └── ui/                 # PyQt6 interface
│       ├── theme.py        #   dark QSS theme + accents
│       ├── icons.py        #   inline-SVG icon set
│       ├── widgets.py      #   sidebar buttons, chips, path rows, console…
│       ├── worker.py       #   QThread workers (recovery + chatwork)
│       ├── page_dashboard.py / page_recover.py / page_chatwork.py / page_report.py / page_settings.py
│       └── main_window.py
├── tools/
│   ├── make_fixture.py     # synthetic .codex fixture generator (all edge cases)
│   ├── make_chatwork_fixture.py  # synthetic chatgpt catalog/browser/export fixture
│   └── ui_snapshot.py      # offscreen screenshots for docs/
├── tests/
│   ├── test_core.py        # 46 end-to-end assertions, no Qt needed
│   ├── test_chatwork.py    # 42 Chat & Work engine + report assertions
│   └── test_gui_offscreen.py
└── docs/
    ├── sample_report.md    # report generated from the synthetic fixture
    └── ui_*.png
```

## Testing

```bash
python3 tests/test_core.py                          # engine + redaction matrix
python3 tests/test_chatwork.py                      # Chat & Work engine + report
QT_QPA_PLATFORM=offscreen python3 tests/test_gui_offscreen.py   # GUI smoke (Linux/CI)
python3 tools/make_fixture.py --out /tmp/fakecodex  # build a mock .codex
python3 tools/make_chatwork_fixture.py --out /tmp/fakecw  # mock chatgpt fixtures
```

The fixture mirrors the real layout including a multi-MB transcript, corrupt lines,
an empty session, a ghost index entry, encrypted reasoning and planted PII
(emails, JWTs, API keys, SIDs, git credentials) so leaks are provable.

## Sample output

See [`docs/sample_report.md`](docs/sample_report.md) — generated from the synthetic fixture:
session index table, application state (config, workspaces, MCP servers, prompt history),
per-session turn-by-turn conversations with tool I/O, token tables, filtered logs,
statistics and a parse-warnings appendix.

## Disclaimer

DeKodX recovers data you already own from your own machine. Encrypted reasoning
blocks are ciphertext by design and are marked unrecoverable rather than attacked.
Reports can still contain sensitive *content* (your prompts and tool outputs) —
store them accordingly, or use **Paranoid** before sharing.
