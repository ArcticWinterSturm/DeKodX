"""DeKodX main window: sidebar navigation + stacked pages + background worker."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, APP_TAGLINE, __version__
from ..core import scanner
from . import icons, theme
from .page_chatwork import ChatWorkPage
from ._paths import archive_report
from .page_dashboard import DashboardPage
from .page_livetrack import LiveTrapPage
from .page_recover import RecoverPage
from .page_report import ReportPage
from .page_settings import SettingsPage
from .widgets import SideBarButton, rich_tip
from .worker import ChatWorkWorker, RecoveryWorker


class MainWindow(QMainWindow):
    def __init__(self, initial_source: str = ""):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} - {APP_TAGLINE}")
        self.setWindowIcon(icons.icon("logo", "#3fd68f", 64))
        self.resize(1240, 820)
        self.setMinimumSize(1020, 680)
        self.worker: RecoveryWorker | None = None

        central = QWidget(self)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # -- sidebar ---------------------------------------------------------- #
        sidebar = QWidget(central)
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(216)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(14, 18, 14, 14)
        side.setSpacing(6)

        logo_row = QHBoxLayout()
        logo_icon = logo_glyph(sidebar)
        logo_row.addWidget(logo_icon)
        logo_text = QVBoxLayout()
        logo = QLabel(APP_NAME, sidebar)
        logo.setObjectName("LogoLabel")
        logo.setToolTip(rich_tip(APP_NAME, APP_TAGLINE + " - local-only forensic recovery."))
        sub = QLabel("SESSION RECOVERY", sidebar)
        sub.setObjectName("LogoSub")
        logo_text.addWidget(logo)
        logo_text.addWidget(sub)
        logo_row.addLayout(logo_text)
        logo_row.addStretch(1)
        side.addLayout(logo_row)
        side.addSpacing(16)

        self.nav_buttons = [
            SideBarButton("dashboard", "Archive", "Archive",
                          "Auto-detect the Codex data directory and inventory sessions, "
                          "databases and tokens before recovering."),
            SideBarButton("recover", "Depth Level", "Depth Level",
                          "Configure content, redaction level and output, then run the "
                          "recovery with live progress and log."),
            SideBarButton("livetrack", "Live Tracking", "Live Tracking",
                          "Audit Mode (red dot): passive read-only dry run. Live Mode "
                          "(green dot): intercept Codex — TTFT, tokens, rate limits, "
                          "transitory reasoning, ns-precision network telemetry, byte "
                          "counters. Session .md report on stop."),
            SideBarButton("chatwork", "Chat & Work", "Chat & Work archaeology",
                          "Scan the local footprint of your chatgpt.com (non-Codex) account: "
                          "thread catalog bridge, chatgpt-linked rollouts, browser traces "
                          "and export archives."),
            SideBarButton("report", "Report", "Report",
                          "Preview the generated Markdown report, open it in your editor or "
                          "reveal it in Explorer."),
            SideBarButton("settings", "Settings", "Settings & About",
                          "Defaults, accent colour, and everything DeKodX will and will not "
                          "write into a report."),
        ]
        for index, button in enumerate(self.nav_buttons):
            button.clicked.connect(lambda _=False, i=index: self.show_page(i))
            side.addWidget(button)
        side.addStretch(1)
        version = QLabel(f"v{__version__}  •  local only", sidebar)
        version.setObjectName("SideVersion")
        side.addWidget(version)
        root.addWidget(sidebar)

        # -- pages -------------------------------------------------------------- #
        self.stack = QStackedWidget(central)
        self.dashboard = DashboardPage(self)
        self.recover = RecoverPage(self)
        self.livetrack = LiveTrapPage(self)
        self.chatwork = ChatWorkPage(self)
        self.report = ReportPage(self)
        self.settings = SettingsPage(self)
        for page in (self.dashboard, self.recover, self.livetrack, self.chatwork,
                     self.report, self.settings):
            self.stack.addWidget(page)
        root.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        # -- status bar ----------------------------------------------------------- #
        status = QStatusBar(self)
        self.status_label = QLabel("Ready", status)
        status.addWidget(self.status_label, 1)
        self.setStatusBar(status)

        # -- wiring ----------------------------------------------------------------- #
        self.dashboard.goto_recover.connect(self.start_from_dashboard)
        self.recover.start_requested.connect(self.begin_recovery)
        self.recover.cancel_requested.connect(self.cancel_recovery)
        self.chatwork.start_requested.connect(self.begin_chatwork)
        self.chatwork.cancel_requested.connect(self.cancel_chatwork)
        self.chatwork.open_report.connect(self._open_report_file)
        self.settings.accent_changed.connect(self.apply_accent)
        self.livetrack.mode_changed.connect(self._on_trap_mode)

        if initial_source:
            self.recover.source_row.setText(initial_source)
            self.dashboard.source_row.setText(initial_source)
            self.chatwork.codex_row.setText(initial_source)
        self.show_page(0)

    # -- navigation ---------------------------------------------------------- #
    def show_page(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        for i, button in enumerate(self.nav_buttons):
            button.setChecked(i == index)
        self.status_label.setText(
            ("Archive", "Depth Level", "Live Tracking", "Chat & Work", "Report",
             "Settings")[index])

    def _on_trap_mode(self, mode: str) -> None:
        if mode == "live":
            self.status_label.setText("Live Tracking — LIVE interception running")
        elif mode == "audit":
            self.status_label.setText("Live Tracking — AUDIT dry run running")
        else:
            self.status_label.setText("Live Tracking stopped")

    def start_from_dashboard(self, source: str) -> None:
        if source:
            self.recover.source_row.setText(source)
        self.show_page(1)

    def apply_accent(self, name: str) -> None:
        theme.apply(QApplication.instance(), name)

    # -- recovery ---------------------------------------------------------------- #
    def begin_recovery(self, bundle: dict) -> None:
        source = bundle["source"] or str(scanner.detect_default_source())
        output = bundle["output"] or str(archive_report("DeKodX_recovery.md"))
        if not Path(source).is_dir():
            QMessageBox.critical(self, "Invalid source",
                                 f"The source directory does not exist:\n{source}\n\n"
                                 "Pick a valid Codex data directory on the Archive or Depth Level page.")
            return
        self.worker = RecoveryWorker(source, output, bundle["options"], self)
        self.worker.log.connect(self.recover.log)
        self.worker.progress.connect(self.recover.progress.update)
        self.worker.succeeded.connect(self.recovery_done)
        self.worker.failed.connect(self.recovery_failed)
        self.worker.cancelled.connect(self.recovery_cancelled)
        self.worker.finished.connect(self._worker_finished)
        self.recover.set_running(True)
        self.recover.log(f"Recovery started: {source} -> {output}", "ok")
        self.status_label.setText("Recovering…")
        self.worker.start()

    def cancel_recovery(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.recover.log("Cancel requested - finishing current step…", "warn")
            self.worker.cancel()

    def recovery_done(self, path: str) -> None:
        self.recover.log(f"Recovery complete: {path}", "ok")
        if self.worker is not None and self.worker.result is not None:
            per = self.worker.result.per_session_paths
            if per:
                self.recover.log(f"Per-session files: {len(per)} in {Path(path).stem}_sessions/", "ok")
                self.status_label.setText(f"Report + {len(per)} per-session files written")
            else:
                self.status_label.setText(f"Report written: {Path(path).name}")
        else:
            self.status_label.setText(f"Report written: {Path(path).name}")
        self.report.load(path)
        if self.settings.auto_open.isChecked():
            self.show_page(4)

    def recovery_failed(self, message: str) -> None:
        self.recover.log(f"Recovery failed: {message}", "error")
        self.status_label.setText("Recovery failed")
        QMessageBox.critical(self, "Recovery failed", message)

    def recovery_cancelled(self) -> None:
        self.recover.log("Recovery cancelled by user - no report written.", "warn")
        self.status_label.setText("Cancelled")

    # -- chat & work ------------------------------------------------------------- #
    def begin_chatwork(self, bundle: dict) -> None:
        codex_home = bundle.get("codex_home") or str(scanner.detect_default_source())
        if not Path(codex_home).is_dir():
            QMessageBox.critical(
                self, "Invalid source",
                f"The Codex data directory does not exist:\n{codex_home}\n\n"
                "The chatgpt.com thread catalog lives inside ~/.codex — install/sign in "
                "to Codex Desktop or point at a copied .codex directory.")
            return
        self._chatwork_worker = ChatWorkWorker(bundle, self)
        self._chatwork_worker.log.connect(self.chatwork.log)
        self._chatwork_worker.progress.connect(self.chatwork.progress.update)
        self._chatwork_worker.succeeded.connect(self.chatwork_done)
        self._chatwork_worker.failed.connect(self.chatwork_failed)
        self._chatwork_worker.cancelled.connect(self.chatwork_cancelled)
        self._chatwork_worker.finished.connect(self._chatwork_finished)
        self.chatwork.set_running(True)
        self.chatwork.log(f"Chat & Work scan started: {codex_home}", "ok")
        self.status_label.setText("Scanning Chat & Work…")
        self._chatwork_worker.start()

    def cancel_chatwork(self) -> None:
        worker = getattr(self, "_chatwork_worker", None)
        if worker is not None and worker.isRunning():
            self.chatwork.log("Cancel requested - finishing current step…", "warn")
            worker.cancel()

    def chatwork_done(self, path: str, stats: dict) -> None:
        self.chatwork.scan_done(path)
        self.chatwork.log(
            f"Chat & Work report complete: {path}", "ok")
        self.chatwork.log(
            f"Catalog threads: {stats.get('catalog_threads', 0)} "
            f"(tpp: {stats.get('tpp_threads', 0)}) · rollouts linked: "
            f"{stats.get('rollout_linked', 0)} · browser profiles: "
            f"{stats.get('browsers', 0)} · exports: {stats.get('exports', 0)}", "ok")
        self.status_label.setText(f"Chat & Work report: {Path(path).name}")
        self.report.load(path)

    def chatwork_failed(self, message: str) -> None:
        self.chatwork.log(f"Chat & Work scan failed: {message}", "error")
        self.status_label.setText("Chat & Work scan failed")
        QMessageBox.critical(self, "Chat & Work scan failed", message)

    def chatwork_cancelled(self) -> None:
        self.chatwork.log("Scan cancelled by user.", "warn")
        self.status_label.setText("Cancelled")

    def _chatwork_finished(self) -> None:
        self.chatwork.set_running(False)
        self._chatwork_worker = None

    def _open_report_file(self, path: str) -> None:
        if path and Path(path).exists():
            self.report.load(path)
            self.show_page(4)

    def _worker_finished(self) -> None:
        self.recover.set_running(False)
        self.worker = None

    # -- lifecycle --------------------------------------------------------------- #
    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self.livetrack.shutdown()
        worker = getattr(self, "_chatwork_worker", None)
        if worker is not None and worker.isRunning():
            worker.cancel()
            worker.wait(4000)
        if self.worker is not None and self.worker.isRunning():
            answer = QMessageBox.question(
                self, "Recovery running",
                "A recovery is still running. Cancel it and quit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.worker.cancel()
            self.worker.wait(4000)
        event.accept()


def logo_glyph(parent) -> QLabel:
    label = QLabel(parent)
    label.setPixmap(icons.icon("logo", "#3fd68f", 34).pixmap(34, 34))
    return label
