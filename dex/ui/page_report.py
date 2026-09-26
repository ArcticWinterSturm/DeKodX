"""Report page: preview the generated Markdown and open it externally."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import icons
from .widgets import SectionChip, rich_tip
from .worker import FnWorker


class ReportPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.path: Path | None = None
        self._worker: FnWorker | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)

        head = QHBoxLayout()
        title = QLabel("Report", self)
        title.setProperty("h1", "true")
        head.addWidget(title)
        head.addStretch(1)
        self.open_btn = QPushButton(icons.icon("open", "#9aa3b2", 16), " Open file", self)
        self.open_btn.setToolTip(rich_tip("Open file", "Open the .md in your default Markdown "
                                                       "editor or viewer."))
        self.open_btn.clicked.connect(self.open_file)
        head.addWidget(self.open_btn)
        self.folder_btn = QPushButton(icons.icon("folder", "#9aa3b2", 16), " Show in folder", self)
        self.folder_btn.setToolTip(rich_tip("Show in folder", "Reveal the report in Explorer."))
        self.folder_btn.clicked.connect(self.show_in_folder)
        head.addWidget(self.folder_btn)
        self.copy_btn = QPushButton(icons.icon("copy", "#9aa3b2", 16), " Copy path", self)
        self.copy_btn.setToolTip(rich_tip("Copy path", "Put the report path on the clipboard."))
        self.copy_btn.clicked.connect(self.copy_path)
        head.addWidget(self.copy_btn)
        layout.addLayout(head)

        self.meta = QLabel("No report generated yet - run a recovery first.", self)
        self.meta.setProperty("muted", "true")
        layout.addWidget(self.meta)

        layout.addWidget(SectionChip("Preview", "blue"))
        self.preview = QPlainTextEdit(self)
        self.preview.setProperty("preview", "true")
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("The recovered Markdown report will appear here…")
        self.preview.setToolTip(rich_tip("Preview", "Read-only preview of the written report. "
                                                     "Loaded on a background thread for big files."))
        layout.addWidget(self.preview, 1)

    # -- behaviour ----------------------------------------------------------- #
    def load(self, path: str) -> None:
        self.path = Path(path)
        self.meta.setText(f"Loading {self.path} …")
        self._worker = FnWorker(_read, self.path, parent=self)
        self._worker.result.connect(self._show)
        self._worker.failed.connect(lambda msg: self.meta.setText(f"Could not read report: {msg}"))
        self._worker.start()

    def _show(self, payload: tuple) -> None:
        text, size = payload
        self.preview.setPlainText(text)
        self.preview.verticalScrollBar().setValue(0)
        self.meta.setText(f"{self.path}  •  {size:,} bytes  •  {len(text.splitlines()):,} lines")

    def open_file(self) -> None:
        if self.path and self.path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.path)))

    def show_in_folder(self) -> None:
        if self.path and self.path.parent.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.path.parent)))

    def copy_path(self) -> None:
        if self.path:
            QApplication.clipboard().setText(str(self.path))


def _read(path: Path) -> tuple[str, int]:
    size = path.stat().st_size
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read(), size
