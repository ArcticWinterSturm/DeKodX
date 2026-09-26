"""Dark application theme - QSS stylesheet + palette management.

Visual language borrowed from the author's LVS tooling: deep charcoal panels,
tinted monospace path fields, accent-coloured section chips and icon buttons.
"""

from __future__ import annotations

ACCENTS = {
    "Kodx Green": "#3fd68f",
    "Signal Blue": "#6ea8fe",
    "Amber": "#ffb454",
    "Violet": "#b18cff",
}
DEFAULT_ACCENT = "Kodx Green"

_BASE = {
    "bg": "#131519",
    "panel": "#1a1d23",
    "panel2": "#232730",
    "border": "#2c313b",
    "text": "#d8dee8",
    "muted": "#8a93a4",
    "field": "#1c2621",
    "console": "#0f1114",
    "blue": "#6ea8fe",
    "amber": "#ffb454",
    "red": "#ff5d5d",
}


def _rgba(hex_color: str, alpha: float) -> str:
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r}, {g}, {b}, {alpha:.2f})"


def _lighten(hex_color: str, factor: float = 0.18) -> str:
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    r = int(r + (255 - r) * factor)
    g = int(g + (255 - g) * factor)
    b = int(b + (255 - b) * factor)
    return f"#{r:02x}{g:02x}{b:02x}"


_QSS = """
QWidget { background-color: $bg; color: $text;
          font-family: "Segoe UI", "Inter", "Helvetica Neue", system-ui, sans-serif;
          font-size: 13px; }
QMainWindow, QDialog { background-color: $bg; }
QLabel { background: transparent; }

#Sidebar { background-color: $panel; border-right: 1px solid $border; }
#LogoLabel { font-size: 21px; font-weight: 800; letter-spacing: 1px; }
#LogoSub { color: $muted; font-size: 10px; letter-spacing: 2px; }
#SideVersion { color: $muted; font-size: 10px; }

QToolButton[nav="true"] {
    background: transparent; border: none; border-left: 3px solid transparent;
    border-radius: 7px; padding: 11px 12px; text-align: left; color: $muted; font-size: 13px;
}
QToolButton[nav="true"]:hover { background-color: $panel2; color: $text; }
QToolButton[nav="true"]:checked {
    background-color: $panel2; color: $text; border-left: 3px solid $accent; font-weight: 600;
}

QPushButton {
    background-color: $panel2; border: 1px solid $border; border-radius: 8px;
    padding: 8px 16px; color: $text; font-weight: 600;
}
QPushButton:hover { border-color: $accent; }
QPushButton:pressed { background-color: $accent_dim; }
QPushButton:disabled { color: $muted; background-color: $panel; border-color: $border; }
QPushButton[primary="true"] { background-color: $accent; color: #07130c; border: none; font-weight: 700; }
QPushButton[primary="true"]:hover { background-color: $accent_hi; }
QPushButton[primary="true"]:disabled { background-color: $panel2; color: $muted; }
QPushButton[danger="true"] { background: transparent; color: $red; border: 1px solid $red; }
QPushButton[danger="true"]:hover { background-color: rgba(255, 93, 93, 0.12); }
QPushButton[mini="true"] { padding: 3px 9px; border-radius: 6px; font-weight: 700; }

QLineEdit, QSpinBox, QComboBox {
    background-color: $field; border: 1px solid $border; border-radius: 7px;
    padding: 7px 10px; color: $text; selection-background-color: $accent;
    selection-color: #07130c;
}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border-color: $accent; }
QLineEdit:read-only { color: $muted; }
QLineEdit[mono="true"], QPlainTextEdit[mono="true"] {
    font-family: "Cascadia Code", "Consolas", "Menlo", monospace; font-size: 12px;
}
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView {
    background-color: $panel2; border: 1px solid $border; color: $text;
    selection-background-color: $accent_dim; selection-color: $accent;
}

QCheckBox, QRadioButton { spacing: 9px; background: transparent; padding: 3px 0; }
QCheckBox::indicator, QRadioButton::indicator {
    width: 16px; height: 16px; border: 1px solid $border; background-color: $panel2;
}
QCheckBox::indicator { border-radius: 4px; }
QRadioButton::indicator { border-radius: 9px; }
QCheckBox::indicator:checked, QRadioButton::indicator:checked {
    background-color: $accent; border-color: $accent;
}
QCheckBox::indicator:checked:hover, QRadioButton::indicator:checked:hover {
    background-color: $accent_hi;
}

QProgressBar {
    background-color: $panel2; border: none; border-radius: 7px; height: 15px;
    text-align: center; color: $text; font-size: 10px; font-weight: 600;
}
QProgressBar::chunk { background-color: $accent; border-radius: 7px; }

QPlainTextEdit[console="true"] {
    background-color: $console; border: 1px solid $border; border-radius: 8px;
    font-family: "Cascadia Code", "Consolas", "Menlo", monospace; font-size: 12px;
}
QPlainTextEdit[preview="true"] {
    background-color: $console; border: 1px solid $border; border-radius: 8px;
    font-family: "Cascadia Code", "Consolas", "Menlo", monospace; font-size: 12px;
}

QFrame[card="true"] { background-color: $panel; border: 1px solid $border; border-radius: 10px; }
#CardValue { font-size: 24px; font-weight: 800; color: $accent; }
#CardCaption { color: $muted; font-size: 10px; letter-spacing: 1px; }

QLabel[chip="green"] { background-color: $accent_dim; color: $accent; border-radius: 5px;
                       padding: 3px 10px; font-weight: 700; }
QLabel[chip="blue"] { background-color: rgba(110, 168, 254, 0.15); color: $blue;
                      border-radius: 5px; padding: 3px 10px; font-weight: 700; }
QLabel[chip="amber"] { background-color: rgba(255, 180, 84, 0.15); color: $amber;
                       border-radius: 5px; padding: 3px 10px; font-weight: 700; }
QLabel[chip="red"] { background-color: rgba(255, 93, 93, 0.15); color: $red;
                     border-radius: 5px; padding: 3px 10px; font-weight: 700; }
QLabel[h1="true"] { font-size: 22px; font-weight: 800; }
QLabel[h2="true"] { font-size: 15px; font-weight: 700; }
QLabel[muted="true"] { color: $muted; }

QListWidget {
    background-color: $panel; border: 1px solid $border; border-radius: 8px; outline: none;
}
QListWidget::item { padding: 7px 9px; border-bottom: 1px solid $border; }
QListWidget::item:selected { background-color: $accent_dim; color: $accent; }

QScrollBar:vertical { background: transparent; width: 11px; margin: 3px; }
QScrollBar::handle:vertical { background-color: #3a4150; border-radius: 5px; min-height: 28px; }
QScrollBar::handle:vertical:hover { background-color: #4a5262; }
QScrollBar:horizontal { background: transparent; height: 11px; margin: 3px; }
QScrollBar::handle:horizontal { background-color: #3a4150; border-radius: 5px; min-width: 28px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QStatusBar { background-color: $panel; border-top: 1px solid $border; color: $muted; }
QStatusBar::item { border: none; }

QToolTip {
    background-color: $panel2; color: $text; border: 1px solid $accent;
    border-radius: 8px; padding: 9px 12px; font-size: 12px; max-width: 340px;
}
QMessageBox { background-color: $panel; }
QSplitter::handle { background-color: $border; }
"""


def build_qss(accent_name: str = DEFAULT_ACCENT) -> str:
    accent = ACCENTS.get(accent_name, ACCENTS[DEFAULT_ACCENT])
    palette = dict(_BASE)
    palette.update(
        accent=accent,
        accent_hi=_lighten(accent),
        accent_dim=_rgba(accent, 0.16),
    )
    qss = _QSS
    for key in sorted(palette, key=len, reverse=True):   # longest first: $panel2 before $panel
        qss = qss.replace("$" + key, palette[key])
    return qss


def apply(app, accent_name: str = DEFAULT_ACCENT) -> None:
    app.setStyleSheet(build_qss(accent_name))
