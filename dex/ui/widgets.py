"""Reusable custom widgets: sidebar nav, section chips, path rows, cards, console."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import icons


def rich_tip(title: str, body: str) -> str:
    """Rich-text tooltip markup (title + description), LVS-style popup."""
    return (
        f"<div style='margin:0'><b style='color:#3fd68f'>{title}</b><br/>"
        f"<span style='color:#c6cdd8'>{body}</span></div>"
    )


class SideBarButton(QToolButton):
    def __init__(self, icon_name: str, text: str, tip_title: str, tip_body: str, parent=None):
        super().__init__(parent)
        self.setProperty("nav", "true")
        self.setCheckable(True)
        self.setIcon(icons.icon(icon_name, "#9aa3b2", 18))
        self.setText(f"  {text}")
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(rich_tip(tip_title, tip_body))
        self.setIconSize(self.iconSize().scaled(18, 18, Qt.AspectRatioMode.KeepAspectRatio))


class SectionChip(QLabel):
    """Accent-coloured section header chip (LVS schema)."""

    def __init__(self, text: str, color: str = "green", parent=None):
        super().__init__(text, parent)
        self.setProperty("chip", color)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.setToolTip(rich_tip(text, "Section of this page - hover controls below for details."))


class PathRow(QWidget):
    """Line edit tinted LVS-green with clear (x) and browse (...) buttons."""

    textChanged = pyqtSignal(str)

    def __init__(self, placeholder: str = "", mode: str = "dir", parent=None):
        super().__init__(parent)
        self.mode = mode
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.edit = QLineEdit(self)
        self.edit.setProperty("mono", "true")
        self.edit.setPlaceholderText(placeholder)
        self.edit.textChanged.connect(self.textChanged.emit)
        self.edit.setToolTip(rich_tip("Path", "Type a path or use the browse button. "
                                               "The x button clears the field."))
        layout.addWidget(self.edit, 1)
        self.clear_btn = QPushButton("×", self)
        self.clear_btn.setProperty("mini", "true")
        self.clear_btn.setFixedWidth(30)
        self.clear_btn.setToolTip(rich_tip("Clear", "Empty this path field."))
        self.clear_btn.clicked.connect(lambda: self.edit.clear())
        layout.addWidget(self.clear_btn)
        self.browse_btn = QPushButton("…", self)
        self.browse_btn.setProperty("mini", "true")
        self.browse_btn.setFixedWidth(30)
        self.browse_btn.setToolTip(rich_tip("Browse…", "Open a filesystem picker dialog."))
        self.browse_btn.clicked.connect(self.browse)
        layout.addWidget(self.browse_btn)

    def text(self) -> str:
        return self.edit.text().strip()

    def setText(self, value: str) -> None:
        self.edit.setText(value)

    def browse(self) -> None:
        if self.mode == "dir":
            picked = QFileDialog.getExistingDirectory(self, "Select Codex data directory",
                                                      self.text() or str(Path.home()))
        else:
            picked, _ = QFileDialog.getSaveFileName(
                self, "Save recovery report", self.text() or str(Path.home() / "DeKodX_recovery.md"),
                "Markdown (*.md);;All files (*)")
        if picked:
            self.edit.setText(Path(picked).as_posix() if self.mode == "file" else picked)


class StatCard(QFrame):
    def __init__(self, caption: str, value: str = "—", parent=None):
        super().__init__(parent)
        self.setProperty("card", "true")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(2)
        self.value_label = QLabel(value, self)
        self.value_label.setObjectName("CardValue")
        self.caption_label = QLabel(caption.upper(), self)
        self.caption_label.setObjectName("CardCaption")
        layout.addWidget(self.value_label)
        layout.addWidget(self.caption_label)

    def set_value(self, value: str, tip: str = "") -> None:
        self.value_label.setText(value)
        if tip:
            self.setToolTip(rich_tip(self.caption_label.text().title(), tip))


class ConsoleLog(QPlainTextEdit):
    LEVEL_COLORS = {"info": "#c6cdd8", "ok": "#3fd68f", "warn": "#ffb454", "error": "#ff5d5d"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("console", "true")
        self.setReadOnly(True)
        self.setMaximumBlockCount(4000)

    def append_log(self, message: str, level: str = "info") -> None:
        color = self.LEVEL_COLORS.get(level, "#c6cdd8")
        safe = (message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
        self.appendHtml(f'<span style="color:#5c6575">[{_stamp()}]</span> '
                        f'<span style="color:{color}">{safe}</span>')
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())


class ProgressWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.bar = QProgressBar(self)
        self.bar.setRange(0, 1000)
        self.bar.setFormat("")
        self.bar.setToolTip(rich_tip("Progress", "Live recovery progress across scan, parse, "
                                                 "redaction and report writing."))
        row.addWidget(self.bar, 1)
        self.pct = QLabel("0%", self)
        self.pct.setFixedWidth(44)
        row.addWidget(self.pct)
        layout.addLayout(row)
        self.status = QLabel("Idle", self)
        self.status.setProperty("muted", "true")
        layout.addWidget(self.status)

    def update(self, frac: float, label: str) -> None:  # noqa: A003 - Qt-style name
        value = int(max(0.0, min(frac, 1.0)) * 1000)
        self.bar.setValue(value)
        self.pct.setText(f"{value // 10}%")
        if label:
            self.status.setText(label)

    def reset(self) -> None:
        self.bar.setValue(0)
        self.pct.setText("0%")
        self.status.setText("Idle")


class LabeledRow(QWidget):
    """Left label + widget row, like the LVS path grid."""

    def __init__(self, label: str, widget: QWidget, tip: str = "", parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        caption = QLabel(label, self)
        caption.setFixedWidth(110)
        caption.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        caption.setProperty("muted", "true")
        layout.addWidget(caption)
        layout.addWidget(widget, 1)
        if tip:
            self.setToolTip(rich_tip(label, tip))


def mono_font() -> QFont:
    font = QFont("Cascadia Code")
    font.setStyleHint(QFont.StyleHint.Monospace)
    return font


def _stamp() -> str:
    import time
    return time.strftime("%H:%M:%S")


__all__ = [
    "ConsoleLog", "LabeledRow", "PathRow", "ProgressWidget", "SectionChip",
    "SideBarButton", "StatCard", "rich_tip", "mono_font", "QComboBox",
]
