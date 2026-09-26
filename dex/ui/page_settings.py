"""Settings & About page."""

from __future__ import annotations

import platform
import sys

from PyQt6.QtCore import QT_VERSION_STR, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..core import scanner
from . import theme
from .widgets import PathRow, SectionChip, rich_tip


class SettingsPage(QWidget):
    accent_changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)

        title = QLabel("Settings", self)
        title.setProperty("h1", "true")
        layout.addWidget(title)

        layout.addWidget(SectionChip("Defaults"))
        row = QHBoxLayout()
        caption = QLabel("Output dir:", self)
        caption.setProperty("muted", "true")
        caption.setFixedWidth(80)
        row.addWidget(caption)
        self.default_out = PathRow(str(Path_home_desktop()), mode="dir", parent=self)
        self.default_out.setToolTip(rich_tip(
            "Default output directory", "Suggested folder pre-filled on the Depth Level page for new "
                                         "reports."))
        row.addWidget(self.default_out, 1)
        layout.addLayout(row)

        self.auto_open = QCheckBox("Switch to the Report page automatically when a recovery finishes", self)
        self.auto_open.setChecked(True)
        self.auto_open.setToolTip(rich_tip("Auto-open", "Jump to the built-in preview as soon as "
                                                        "the report is written."))
        layout.addWidget(self.auto_open)

        accent_row = QHBoxLayout()
        accent_caption = QLabel("Accent:", self)
        accent_caption.setProperty("muted", "true")
        accent_caption.setFixedWidth(80)
        accent_row.addWidget(accent_caption)
        self.accent_combo = QComboBox(self)
        self.accent_combo.addItems(list(theme.ACCENTS.keys()))
        self.accent_combo.setToolTip(rich_tip("Accent colour", "Restyles the whole application "
                                                               "live - sidebar, chips, buttons."))
        self.accent_combo.currentTextChanged.connect(self.accent_changed.emit)
        accent_row.addWidget(self.accent_combo, 1)
        layout.addLayout(accent_row)
        layout.addSpacing(8)

        layout.addWidget(SectionChip("About", "blue"))
        about = QTextBrowser(self)
        about.setOpenExternalLinks(False)
        about.setStyleSheet("QTextBrowser { background: #1a1d23; border: 1px solid #2c313b; "
                            "border-radius: 10px; padding: 14px; }")
        about.setHtml(f"""
        <div style="font-family:'Segoe UI',sans-serif; color:#d8dee8; font-size:13px;">
          <h2 style="color:#3fd68f; margin-top:0;">DeKodX {__version__}</h2>
          <p>{'Codex Desktop Session Recovery - a local-only forensic utility.'}</p>
          <p>Reads <code>~/.codex</code> (JSONL rollouts, state_5.sqlite, logs_2.sqlite,
             codex-dev.db, config.toml, auth.json, global state), reconstructs every conversation
             with turns, tool calls, commentary and token usage, redacts PII and secrets, and
             writes one navigable Markdown report.</p>
          <ul>
            <li><b>Privacy:</b> runs entirely offline; no network calls, ever.</li>
            <li><b>Safety:</b> databases are opened read-only; your Codex install is never modified.</li>
            <li><b>Hard secrets</b> (auth tokens, API keys, installation id, SIDs, account ids)
                are redacted at every redaction level, including <i>None</i>.</li>
          </ul>
          <p style="color:#8a93a4;">Python {platform.python_version()} &nbsp;•&nbsp;
             Qt {QT_VERSION_STR} &nbsp;•&nbsp; {platform.system()} {platform.release()}</p>
          <p style="color:#8a93a4;">Default source: <code>{scanner.detect_default_source()}</code></p>
        </div>""")
        layout.addWidget(about, 1)


def Path_home_desktop():  # noqa: N802 - reads naturally at call site
    from pathlib import Path
    desktop = Path.home() / "Desktop"
    return desktop if desktop.is_dir() else Path.home() / "Documents"
