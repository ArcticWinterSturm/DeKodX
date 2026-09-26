"""Dashboard page: auto-detect the .codex install and show headline numbers."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..core import scanner
from . import icons
from .widgets import PathRow, SectionChip, StatCard, rich_tip
from .worker import FnWorker

FILE_LABELS = {
    "session_index.jsonl": "Session index (JSONL)",
    "state_5.sqlite": "State database (SQLite)",
    "logs_2.sqlite": "Log database (SQLite)",
    "config.toml": "Configuration (TOML)",
    "auth.json": "Auth tokens (secrets - redacted)",
    ".codex-global-state.json": "Global app state (JSON)",
    "installation_id": "Installation id (redacted)",
    "cap_sid": "Sandbox SID map (redacted)",
    "thread_history_1.sqlite": "Thread history (SQLite)",
    "goals_1.sqlite": "Goals (SQLite)",
    "queue_1.sqlite": "Follow-up queue (SQLite)",
    "memories_1.sqlite": "Memories (SQLite)",
}


class DashboardPage(QWidget):
    goto_recover = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: FnWorker | None = None
        self._scanned_once = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(16)

        head = QHBoxLayout()
        title = QLabel("Archive", self)
        title.setProperty("h1", "true")
        head.addWidget(title)
        head.addStretch(1)
        self.rescan_btn = QPushButton(icons.icon("refresh", "#9aa3b2", 16), " Rescan", self)
        self.rescan_btn.setToolTip(rich_tip("Rescan", "Re-read the source directory and refresh "
                                                      "every number on this page."))
        self.rescan_btn.clicked.connect(lambda: self.scan(self.source_row.text()))
        head.addWidget(self.rescan_btn)
        outer.addLayout(head)

        subtitle = QLabel("Auto-detects your Codex Desktop data directory and inventories every "
                          "storage format before recovery.", self)
        subtitle.setProperty("muted", "true")
        subtitle.setWordWrap(True)
        outer.addWidget(subtitle)

        outer.addWidget(SectionChip("Source directory"))
        src_row = QHBoxLayout()
        self.source_row = PathRow("C:\\Users\\you\\.codex  (or ~/.codex)", mode="dir", parent=self)
        self.source_row.setToolTip(rich_tip(
            "Source directory",
            "The .codex installation to recover from. Defaults to ~/.codex - use browse to "
            "point elsewhere, e.g. a copied forensic image."))
        src_row.addWidget(self.source_row, 1)
        detect_btn = QPushButton(icons.icon("search", "#9aa3b2", 16), " Auto-detect", self)
        detect_btn.setToolTip(rich_tip("Auto-detect", "Fill the field with the detected default "
                                                      "Codex data directory and scan it."))
        detect_btn.clicked.connect(self.detect)
        src_row.addWidget(detect_btn)
        outer.addLayout(src_row)

        cards = QGridLayout()
        cards.setSpacing(12)
        self.cards = {
            "sessions": StatCard("Sessions"),
            "archived": StatCard("Archived"),
            "subagents": StatCard("Subagents"),
            "tokens": StatCard("Tokens used"),
            "size": StatCard("Data size"),
            "logs": StatCard("Log rows"),
            "range": StatCard("Date range"),
        }
        for col, card in enumerate(self.cards.values()):
            cards.addWidget(card, 0, col)
        outer.addLayout(cards)

        # Version info
        outer.addWidget(SectionChip("Version Detection", "amber"))
        self.version_label = QLabel("Scanning…", self)
        self.version_label.setProperty("muted", "true")
        self.version_label.setWordWrap(True)
        outer.addWidget(self.version_label)

        outer.addWidget(SectionChip("Inventory", "blue"))
        self.inventory = QListWidget(self)
        self.inventory.setToolTip(rich_tip(
            "Inventory", "Expected Codex files and whether they were found. Missing optional "
                         "files are skipped gracefully during recovery."))
        self.inventory.setMaximumHeight(220)
        outer.addWidget(self.inventory)

        self.issues = QLabel("", self)
        self.issues.setProperty("muted", "true")
        self.issues.setWordWrap(True)
        outer.addLayout(_stretch_row(self.issues))

        foot = QHBoxLayout()
        foot.addStretch(1)
        go = QPushButton(icons.icon("recover", "#07130c", 16), " Start a recovery →", self)
        go.setProperty("primary", "true")
        go.setToolTip(rich_tip("Start a recovery", "Jump to the Depth Level page with this source "
                                                   "directory pre-filled."))
        go.clicked.connect(lambda: self.goto_recover.emit(self.source_row.text()))
        foot.addWidget(go)
        outer.addLayout(foot)
        outer.addStretch(1)

    # -- behaviour ---------------------------------------------------------- #
    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().showEvent(event)
        if not self._scanned_once:
            self._scanned_once = True
            if self.source_row.text():
                self.scan(self.source_row.text())
            else:
                self.detect()

    def detect(self) -> None:
        detected = scanner.detect_default_source()
        self.source_row.setText(str(detected))
        self.scan(str(detected))

    def scan(self, source: str) -> None:
        if not source:
            return
        self.rescan_btn.setEnabled(False)
        self.issues.setText("Scanning…")
        self._worker = FnWorker(scanner.quick_stats, Path(source), parent=self)
        self._worker.result.connect(self._on_stats)
        self._worker.failed.connect(self._on_fail)
        self._worker.start()

    def _on_fail(self, message: str) -> None:
        self.rescan_btn.setEnabled(True)
        self.issues.setText(f"Scan failed: {message}")

    def _on_stats(self, stats: dict) -> None:
        self.rescan_btn.setEnabled(True)
        self.cards["sessions"].set_value(
            str(max(stats["index_sessions"], stats["active_transcripts"] + stats["archived_transcripts"])),
            f"{stats['index_sessions']} index entries, {stats['active_transcripts']} active and "
            f"{stats['archived_transcripts']} archived transcripts on disk")
        self.cards["archived"].set_value(str(stats["archived_transcripts"]),
                                         "Transcripts under archived_sessions/")
        self.cards["subagents"].set_value(str(stats.get("subagent_candidates", 0)),
                                          "Detected subagent/guardian sessions")
        self.cards["tokens"].set_value(f"{stats['tokens_used']:,}", "Sum of threads.tokens_used in state_5.sqlite")
        self.cards["size"].set_value(f"{stats['size_mb']} MB", "Combined size of JSONL/SQLite/JSON/TOML data")
        self.cards["logs"].set_value(f"{stats['log_rows']:,}", "Rows in logs_2.sqlite")
        low, high = stats["date_range"]
        self.cards["range"].set_value(f"{low[5:] or '—'} → {high[5:] or '—'}" if low else "—",
                                      "Earliest to latest thread activity")

        # Version info
        version_parts = []
        if stats.get("app_version"):
            version_parts.append(f"App: {stats['app_version']}")
        if stats.get("cli_version"):
            version_parts.append(f"CLI: {stats['cli_version']}")
        if stats.get("config_format"):
            version_parts.append(f"Config: {stats['config_format']}")
        if stats.get("state_db_version"):
            version_parts.append(f"State DB: v{stats['state_db_version']}")
        if stats.get("logs_db_version"):
            version_parts.append(f"Logs DB: v{stats['logs_db_version']}")
        self.version_label.setText("  •  ".join(version_parts) if version_parts else "No version info detected")

        self.inventory.clear()
        for name, present in stats["present_files"].items():
            label = FILE_LABELS.get(name, name)
            mark = "✓" if present else "✗"
            color = "#3fd68f" if present else "#5c6575"
            self.inventory.addItem(f"{mark}  {name:28s}  {label}")
            item = self.inventory.item(self.inventory.count() - 1)
            item.setForeground(_brush(color))
        self.inventory.addItem(f"✓  sessions/ + archived_sessions/   "
                               f"{stats['active_transcripts']} + {stats['archived_transcripts']} transcripts")
        for extra, flag in (("state_5.sqlite", stats["has_state_db"]),
                            ("logs_2.sqlite", stats["has_logs_db"]),
                            ("sqlite/codex-dev.db", stats["has_dev_db"]),
                            ("thread_history_1.sqlite", stats.get("has_thread_history_db", False)),
                            ("goals_1.sqlite", stats.get("has_goals_db", False)),
                            ("queue_1.sqlite", stats.get("has_queue_db", False)),
                            ("memories_1.sqlite", stats.get("has_memories_db", False))):
            if flag:
                self.inventory.addItem(f"✓  {extra:28s}  readable (opened read-only)")

        issues = list(stats["issues"])
        if not stats["exists"]:
            issues.append("Directory does not exist - pick a valid source before recovering.")
        self.issues.setText("  •  ".join(issues) if issues else
                            "Source looks like a genuine Codex installation. Ready to recover.")


def _stretch_row(widget: QWidget) -> QVBoxLayout:
    layout = QVBoxLayout()
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(widget)
    return layout


def _brush(color: str):
    from PyQt6.QtGui import QColor
    return QColor(color)
