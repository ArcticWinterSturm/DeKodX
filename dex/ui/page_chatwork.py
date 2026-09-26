"""Chat & Work page: chatgpt.com (non-Codex) local archaeology."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..core.chatwork import run_chatwork
from ..core.chatwork_report import build_chatwork_report
from ..core.redaction import Redactor
from . import icons
from ._paths import archive_report
from .widgets import ConsoleLog, PathRow, ProgressWidget, SectionChip, rich_tip


class ChatWorkPage(QWidget):
    """Standalone tab: scan the ChatGPT (non-Codex) local footprint."""

    start_requested = pyqtSignal(dict)      # {codex_home, exports, options…}
    cancel_requested = pyqtSignal()
    open_report = pyqtSignal(str)           # path -> main window report page

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = None
        self._report_path: Path | None = None

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        inner = QWidget(scroll)
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)

        title = QLabel("Chat & Work — chatgpt.com archaeology", inner)
        title.setProperty("h1", "true")
        layout.addWidget(title)
        subtitle = QLabel(
            "Recovers the local footprint of your chatgpt.com (non-Codex) account: the "
            "Codex Desktop thread-catalog bridge, chatgpt-linked rollout transcripts, "
            "browser traces and export archives. Read-only, offline.",
            inner)
        subtitle.setProperty("muted", "true")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        # -- source ----------------------------------------------------------- #
        layout.addWidget(SectionChip("Source"))
        self.codex_row = PathRow("C:\\Users\\you\\.codex  (Codex Desktop data dir)", mode="dir", parent=inner)
        self.codex_row.setToolTip(rich_tip(
            "Codex home", "The ~/.codex directory. The chatgpt.com thread catalog lives in "
            "sqlite/codex-dev.db inside it (synced by Codex Desktop)."))
        layout.addLayout(_row(_caption("Codex dir:", inner), self.codex_row))

        # -- exports ----------------------------------------------------------- #
        layout.addWidget(SectionChip("Export archives", "blue"))
        export_hint = QLabel(
            "Add the .zip / .json / .md files you downloaded from chatgpt.com "
            "(sandbox bundles, shared chats, data exports). Each is inventoried, "
            "hashed and PII-triaged.", inner)
        export_hint.setProperty("muted", "true")
        export_hint.setWordWrap(True)
        layout.addWidget(export_hint)
        self.export_list = QListWidget(inner)
        self.export_list.setMaximumHeight(110)
        self.export_list.setToolTip(rich_tip(
            "Exports", "Files to triage. Sandbox ids, thread references and PII marker "
                        "counts are extracted; nothing is uploaded."))
        layout.addWidget(self.export_list)
        exp_btns = QHBoxLayout()
        add_btn = QPushButton(icons.icon("open", "#9aa3b2", 16), " Add files…", inner)
        add_btn.setToolTip(rich_tip("Add files", "Pick .zip/.json/.md exports to triage."))
        add_btn.clicked.connect(self._add_exports)
        exp_btns.addWidget(add_btn)
        auto_btn = QPushButton(icons.icon("search", "#9aa3b2", 16), " Auto-find Desktop/Downloads", inner)
        auto_btn.setToolTip(rich_tip(
            "Auto-find", "Scan Desktop, Downloads and Documents for likely ChatGPT exports "
                         "(chatgpt-*.zip, *export*.zip, shared-chat saves)."))
        auto_btn.clicked.connect(self._auto_find)
        exp_btns.addWidget(auto_btn)
        rm_btn = QPushButton(icons.icon("cross", "#ff5d5d", 16), " Remove selected", inner)
        rm_btn.setToolTip(rich_tip("Remove", "Drop the selected file from the list."))
        rm_btn.clicked.connect(self._remove_selected)
        exp_btns.addWidget(rm_btn)
        exp_btns.addStretch(1)
        layout.addLayout(exp_btns)

        # -- options ------------------------------------------------------------ #
        layout.addWidget(SectionChip("Scan options", "amber"))
        self.chk_browser = QCheckBox("Scan Chromium browsers for chatgpt.com traces (history, IndexedDB)", inner)
        self.chk_browser.setChecked(True)
        self.chk_browser.setToolTip(rich_tip(
            "Browser scan", "Reads History/IndexedDB/Service-Worker footprints for chatgpt.com "
                            "in Chrome, Edge, Brave, Vivaldi and Opera profiles (read-only)."))
        self.chk_rollouts = QCheckBox("Scan Codex rollouts for chatgpt-linked conversations", inner)
        self.chk_rollouts.setChecked(True)
        self.chk_rollouts.setToolTip(rich_tip(
            "Rollout scan", "Looks through ~/.codex/sessions for transcripts whose metadata "
                            "ties them to a chatgpt.com thread — those have full local bodies."))
        self.chk_redact = QCheckBox("Apply Standard redaction (titles kept, ids trimmed, secrets stripped)", inner)
        self.chk_redact.setChecked(True)
        self.chk_redact.setToolTip(rich_tip(
            "Redaction", "Standard level: emails, tokens, SIDs and home paths redacted; "
                         "thread ids trimmed to 8 chars in tables."))
        for chk in (self.chk_browser, self.chk_rollouts, self.chk_redact):
            layout.addWidget(chk)

        out_row = QHBoxLayout()
        out_row.addWidget(_caption("Output:", inner))
        self.output_row = PathRow(
            str(archive_report("DeKodX_chatwork.md")), mode="file", parent=inner)
        self.output_row.setToolTip(rich_tip(
            "Output", "Where the Chat & Work Markdown report is written."))
        out_row.addWidget(self.output_row, 1)
        layout.addLayout(out_row)

        # -- run ---------------------------------------------------------------- #
        layout.addWidget(SectionChip("Run", "green"))
        actions = QHBoxLayout()
        self.start_btn = QPushButton(icons.icon("play", "#07130c", 16), " Scan Chat & Work", inner)
        self.start_btn.setProperty("primary", "true")
        self.start_btn.setToolTip(rich_tip(
            "Scan", "Run the full Chat & Work archaeology and write the report."))
        self.start_btn.clicked.connect(self._emit_start)
        actions.addWidget(self.start_btn)
        self.cancel_btn = QPushButton(icons.icon("stop", "#ff5d5d", 16), " Cancel", inner)
        self.cancel_btn.setProperty("danger", "true")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setToolTip(rich_tip("Cancel", "Stop after the current step."))
        self.cancel_btn.clicked.connect(self.cancel_requested.emit)
        actions.addWidget(self.cancel_btn)
        self.view_btn = QPushButton(icons.icon("report", "#9aa3b2", 16), " View last report", inner)
        self.view_btn.setToolTip(rich_tip("View report", "Open the last written report in the Report tab."))
        self.view_btn.clicked.connect(self._view_report)
        actions.addWidget(self.view_btn)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.progress = ProgressWidget(inner)
        layout.addWidget(self.progress)

        layout.addWidget(SectionChip("Log", "blue"))
        self.console = ConsoleLog(inner)
        self.console.setMinimumHeight(200)
        layout.addWidget(self.console)

        scroll.setWidget(inner)
        page = QVBoxLayout(self)
        page.setContentsMargins(0, 0, 0, 0)
        page.addWidget(scroll)

    # -- exports ------------------------------------------------------------- #
    def _add_exports(self) -> None:
        picked, _ = QFileDialog.getOpenFileNames(
            self, "Add ChatGPT exports",
            str(Path.home() / "Downloads"),
            "Exports (*.zip *.json *.md *.markdown *.txt);;All files (*)")
        for p in picked:
            if not any(self.export_list.item(i).text() == p for i in range(self.export_list.count())):
                self.export_list.addItem(p)

    def _auto_find(self) -> None:
        home = Path.home()
        patterns = ("chatgpt*.zip", "*export*.zip", "*export*.json",
                    "conversations*.json", "chatgpt*.json", "shared*.zip")
        added = 0
        for folder in (home / "Desktop", home / "Downloads", home / "Documents"):
            if not folder.is_dir():
                continue
            for pat in patterns:
                for p in sorted(folder.glob(pat)):
                    if p.is_file() and not self._has(p):
                        self.export_list.addItem(str(p))
                        added += 1
        self.log(f"Auto-find: {added} candidate export(s) added "
                 "(Desktop / Downloads / Documents)", "ok" if added else "warn")

    def _has(self, path: Path) -> bool:
        return any(self.export_list.item(i).text() == str(path)
                   for i in range(self.export_list.count()))

    def _remove_selected(self) -> None:
        for item in self.export_list.selectedItems():
            self.export_list.takeItem(self.export_list.row(item))

    def export_paths(self) -> list[str]:
        return [self.export_list.item(i).text() for i in range(self.export_list.count())]

    # -- run ------------------------------------------------------------------ #
    def _emit_start(self) -> None:
        self.start_requested.emit({
            "codex_home": self.codex_row.text(),
            "exports": self.export_paths(),
            "output": self.output_row.text(),
            "scan_browser": self.chk_browser.isChecked(),
            "scan_rollouts": self.chk_rollouts.isChecked(),
            "redact": self.chk_redact.isChecked(),
        })

    def set_running(self, running: bool) -> None:
        self.start_btn.setEnabled(not running)
        self.cancel_btn.setEnabled(running)
        for w in (self.codex_row, self.output_row, self.chk_browser,
                  self.chk_rollouts, self.chk_redact):
            w.setEnabled(not running)
        if running:
            self.progress.reset()
            self.progress.status.setText("Starting…")

    def log(self, message: str, level: str = "info") -> None:
        self.console.append_log(message, level)

    def scan_done(self, path: str) -> None:
        self._report_path = Path(path)
        self.progress.update(1.0, "Done")

    def _view_report(self) -> None:
        if self._report_path and self._report_path.exists():
            self.open_report.emit(str(self._report_path))
        else:
            self.log("No report yet — run a scan first.", "warn")


def _caption(text: str, parent) -> QLabel:
    label = QLabel(text, parent)
    label.setProperty("muted", "true")
    label.setFixedWidth(70)
    return label


def _row(label: QWidget, widget: QWidget) -> QHBoxLayout:
    layout = QHBoxLayout()
    layout.setSpacing(10)
    layout.addWidget(label)
    layout.addWidget(widget, 1)
    return layout


# --------------------------------------------------------------------------- #
# worker (runs in QThread via main_window)
# --------------------------------------------------------------------------- #

def chatwork_scan_fn(bundle: dict, log_fn, progress_fn, cancel_fn) -> tuple[str, dict]:
    """Executed off the UI thread; returns (report_path, stats)."""
    from .. import __version__

    codex_home = Path(bundle["codex_home"] or Path.home() / ".codex")
    output = Path(bundle["output"] or str(archive_report("DeKodX_chatwork.md")))
    exports = [Path(p) for p in bundle.get("exports", [])]

    report = run_chatwork(
        codex_home, exports,
        log=log_fn, progress=progress_fn, cancel=cancel_fn,
        scan_browser=bundle.get("scan_browser", True),
        scan_rollout_bodies=bundle.get("scan_rollouts", True),
    )
    level = "standard" if bundle.get("redact", True) else "none"
    redactor = Redactor(level=level, home_dirs=[Path.home()])
    markdown = build_chatwork_report(report, redactor, version=__version__)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(markdown, encoding="utf-8")
    return str(output), report.stats()
