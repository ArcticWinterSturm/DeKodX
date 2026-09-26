"""Recover page: options, redaction level, progress and the live log console."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..core.models import RecoveryOptions
from . import icons
from ._paths import archive_report
from .widgets import ConsoleLog, LabeledRow, PathRow, ProgressWidget, SectionChip, rich_tip

LEVEL_HINTS = {
    "standard": "Spec matrix: tokens, account/install ids, SIDs, emails, home paths and process "
                "UUIDs redacted; session ids trimmed to 8 chars, call ids to 12.",
    "paranoid": "Standard plus: every absolute path, URL and IP stripped, ids trimmed to 4 chars. "
                "Use before sharing a report outside your machine.",
    "none": "Keeps prompts, paths and emails verbatim. Hard secrets (auth tokens, API keys, "
            "installation id, SIDs) are STILL never written. For private offline use only.",
}


class RecoverPage(QWidget):
    start_requested = pyqtSignal(object)   # RecoveryOptions + paths bundle
    cancel_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        inner = QWidget(scroll)
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)

        title = QLabel("Recover", inner)
        title.setProperty("h1", "true")
        layout.addWidget(title)

        # -- paths ----------------------------------------------------------- #
        layout.addWidget(SectionChip("Source & output"))
        self.source_row = PathRow("C:\\Users\\you\\.codex", mode="dir", parent=inner)
        self.source_row.setToolTip(rich_tip(
            "Source directory", "Codex data directory to parse. Read-only access; nothing inside "
                                "is ever modified."))
        layout.addLayout(_row(_caption("Source:", inner), self.source_row))
        self.output_row = PathRow(str(archive_report("DeKodX_recovery.md")), mode="file", parent=inner)
        self.output_row.setToolTip(rich_tip(
            "Output file", "Where the single Markdown recovery report is written. Default: "
                           "Archive/ inside the DeKodX2 launch folder. Parent "
                           "folders are created automatically."))
        layout.addLayout(_row(_caption("Output:", inner), self.output_row))

        # -- content options -------------------------------------------------- #
        layout.addWidget(SectionChip("Content", "blue"))
        self.chk_transcripts = QCheckBox("Include session transcripts (conversation flow)", inner)
        self.chk_transcripts.setChecked(True)
        self.chk_transcripts.setToolTip(rich_tip("Transcripts", "User prompts, assistant replies, "
                                                 "commentary, tool calls and outputs per turn."))
        self.chk_state = QCheckBox("Include application state (config, workspaces, MCP servers)", inner)
        self.chk_state.setChecked(True)
        self.chk_state.setToolTip(rich_tip("Application state", "config.toml, global state, MCP "
                                           "servers, goals and remote-control enrollments."))
        self.chk_tokens = QCheckBox("Include token usage statistics", inner)
        self.chk_tokens.setChecked(True)
        self.chk_tokens.setToolTip(rich_tip("Token usage", "Per-turn input/output/total tables "
                                            "plus session totals."))
        self.chk_tools = QCheckBox("Include tool call details (arguments + outputs)", inner)
        self.chk_tools.setChecked(True)
        self.chk_tools.setToolTip(rich_tip("Tool calls", "function_call / apply_patch / shell / MCP "
                                         "invocations with redacted arguments and outputs."))
        self.chk_logs = QCheckBox("Include logs (last N entries)", inner)
        self.chk_logs.setChecked(True)
        self.chk_logs.setToolTip(rich_tip("Logs", "Tail of logs_2.sqlite with verbose tracing "
                                          "targets filtered out."))
        self.chk_per_session = QCheckBox("Write individual .md files per session", inner)
        self.chk_per_session.setChecked(True)
        self.chk_per_session.setToolTip(rich_tip("Per-session", "Creates a subfolder with one .md "
                         "file per session (named 01_<slug>.md, 02_<slug>.md, …). "
                         "14 sessions = 14 separate files."))
        # -- subagent options ------------------------------------------------- #
        self.chk_subagents = QCheckBox("Include subagent sessions (guardian reviews, parallel workers)", inner)
        self.chk_subagents.setChecked(True)
        self.chk_subagents.setToolTip(rich_tip("Subagents", "Detects and recovers child sessions spawned "
                         "by the main agent (e.g. guardian reviews, parallel workers). "
                         "They are linked to their parent session."))
        self.chk_flatten = QCheckBox("Flatten subagent turns into parent session (inline view)", inner)
        self.chk_flatten.setChecked(False)
        self.chk_flatten.setToolTip(rich_tip("Flatten subagents", "Instead of separate per-session files "
                         "for subagents, show their conversation turns directly inside the "
                         "parent session's section."))

        self.log_limit = QSpinBox(inner)
        self.log_limit.setRange(50, 100000)
        self.log_limit.setSingleStep(250)
        self.log_limit.setValue(1000)
        self.log_limit.setMaximumWidth(240)
        self.log_limit.setToolTip(rich_tip("Log limit", "How many of the most recent log rows to "
                                            "embed in the report."))
        for chk in (self.chk_transcripts, self.chk_state, self.chk_tokens, self.chk_tools,
                    self.chk_logs, self.chk_per_session, self.chk_subagents, self.chk_flatten):
            layout.addWidget(chk)
        log_row = QHBoxLayout()
        log_row.setSpacing(10)
        log_row.addWidget(_caption("Log rows:", inner))
        log_row.addWidget(self.log_limit)
        log_row.addStretch(1)
        layout.addLayout(log_row)

        # # -- redaction --------------------------------------------------------- #
        layout.addWidget(SectionChip("Redaction level", "amber"))
        self.radio_standard = QRadioButton("Standard  -  spec redaction matrix (recommended)", inner)
        self.radio_standard.setChecked(True)
        self.radio_paranoid = QRadioButton("Paranoid  -  also strip paths, URLs and IPs", inner)
        self.radio_none = QRadioButton("None  -  keep text verbatim (hard secrets still hidden)", inner)
        self.level_hint = QLabel(LEVEL_HINTS["standard"], inner)
        self.level_hint.setProperty("muted", "true")
        self.level_hint.setWordWrap(True)
        for radio in (self.radio_standard, self.radio_paranoid, self.radio_none):
            radio.setToolTip(rich_tip("Redaction level", "Controls how much identifying "
                                      "information is stripped from the report."))
            radio.toggled.connect(self._level_changed)
            layout.addWidget(radio)
        layout.addWidget(self.level_hint)

        # -- actions ------------------------------------------------------------- #
        layout.addWidget(SectionChip("Run", "green"))
        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.start_btn = QPushButton(icons.icon("play", "#07130c", 16), " Start Recovery", inner)
        self.start_btn.setProperty("primary", "true")
        self.start_btn.setToolTip(rich_tip("Start Recovery", "Scan, parse, redact and write the "
                                           "Markdown report on a background thread."))
        self.start_btn.clicked.connect(self._emit_start)
        actions.addWidget(self.start_btn)
        self.cancel_btn = QPushButton(icons.icon("stop", "#ff5d5d", 16), " Cancel", inner)
        self.cancel_btn.setProperty("danger", "true")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setToolTip(rich_tip("Cancel", "Stop gracefully after the current parse "
                                              "step; partial reports are not written."))
        self.cancel_btn.clicked.connect(self.cancel_requested.emit)
        actions.addWidget(self.cancel_btn)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.progress = ProgressWidget(inner)
        layout.addWidget(self.progress)

        layout.addWidget(SectionChip("Log", "blue"))
        self.console = ConsoleLog(inner)
        self.console.setMinimumHeight(220)
        layout.addWidget(self.console)

        scroll.setWidget(inner)
        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.addWidget(scroll)

    # -- behaviour ----------------------------------------------------------- #
    def _level_changed(self) -> None:
        level = self.level()
        self.level_hint.setText(LEVEL_HINTS[level])

    def level(self) -> str:
        if self.radio_paranoid.isChecked():
            return "paranoid"
        if self.radio_none.isChecked():
            return "none"
        return "standard"

    def collect_options(self) -> RecoveryOptions:
        return RecoveryOptions(
            include_transcripts=self.chk_transcripts.isChecked(),
            include_app_state=self.chk_state.isChecked(),
            include_tokens=self.chk_tokens.isChecked(),
            include_tools=self.chk_tools.isChecked(),
            include_logs=self.chk_logs.isChecked(),
            log_limit=self.log_limit.value(),
            redaction_level=self.level(),
            per_sessions=self.chk_per_session.isChecked(),
            include_subagents=self.chk_subagents.isChecked(),
            flatten_subagents=self.chk_flatten.isChecked(),
        )

    def _emit_start(self) -> None:
        bundle = {
            "source": self.source_row.text(),
            "output": self.output_row.text(),
            "options": self.collect_options(),
        }
        self.start_requested.emit(bundle)

    def set_running(self, running: bool) -> None:
        self.start_btn.setEnabled(not running)
        self.cancel_btn.setEnabled(running)
        for widget in (self.source_row, self.output_row, self.log_limit):
            widget.setEnabled(not running)
        for chk in (self.chk_transcripts, self.chk_state, self.chk_tokens, self.chk_tools,
                    self.chk_logs, self.chk_per_session, self.chk_subagents, self.chk_flatten,
                    self.radio_standard, self.radio_paranoid, self.radio_none):
            chk.setEnabled(not running)
        if running:
            self.progress.reset()
            self.progress.status.setText("Starting…")

    def log(self, message: str, level: str = "info") -> None:
        self.console.append_log(message, level)


def _caption(text: str, parent) -> QLabel:
    label = QLabel(text, parent)
    label.setProperty("muted", "true")
    label.setFixedWidth(60)
    return label


def _row(label: QWidget, widget: QWidget) -> QHBoxLayout:
    layout = QHBoxLayout()
    layout.setSpacing(10)
    layout.addWidget(label)
    layout.addWidget(widget, 1)
    return layout
