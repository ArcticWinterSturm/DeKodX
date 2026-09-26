"""Inline SVG icon set rendered to QIcon via QtSvg (no external assets)."""

from __future__ import annotations

from functools import lru_cache

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon, QImage, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

_SVG_WRAP = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
    'stroke="$C" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
    "$BODY</svg>"
)

_BODIES = {
    "logo": '<path d="M12 2.5l8 4.75v9.5L12 21.5l-8-4.75v-9.5z"/>'
            '<path d="M9.6 9.2l-2.8 2.8 2.8 2.8M14.4 9.2l2.8 2.8-2.8 2.8"/>',
    "dashboard": '<rect x="3.5" y="3.5" width="7" height="7" rx="1.6"/>'
                 '<rect x="13.5" y="3.5" width="7" height="7" rx="1.6"/>'
                 '<rect x="3.5" y="13.5" width="7" height="7" rx="1.6"/>'
                 '<rect x="13.5" y="13.5" width="7" height="7" rx="1.6"/>',
    "recover": '<path d="M13 2.5L5.5 13.5h5l-1.5 8 7.5-11h-5z" fill="$C" stroke="none"/>',
    "chatwork": '<path d="M4 5.5h16v10.5H13l-4 3.5v-3.5H4z"/>'  # speech bubble (chat)
                '<path d="M7.5 9h9M7.5 12h6"/>',
    "report": '<path d="M6 2.8h8l4 4v14.4H6z"/><path d="M14 2.8v4h4"/>'
              '<path d="M9 12h6M9 15.5h6M9 8.5h2"/>',
    "settings": '<path d="M4 7.5h9M17.5 7.5H20M4 16.5h3M11.5 16.5H20"/>'
                '<circle cx="15" cy="7.5" r="2.2"/><circle cx="9" cy="16.5" r="2.2"/>',
    "folder": '<path d="M3.5 6.5a2 2 0 0 1 2-2h4l2 2.2h7a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>',
    "play": '<path d="M8 5.5v13l10.5-6.5z" fill="$C" stroke="none"/>',
    "stop": '<rect x="7" y="7" width="10" height="10" rx="2.2" fill="$C" stroke="none"/>',
    "open": '<path d="M13.5 4.5H19.5V10.5M19.5 4.5l-8.5 8.5"/>'
            '<path d="M11 5.5H6.5a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V13"/>',
    "refresh": '<path d="M20 12a8 8 0 1 1-2.4-5.7M20 3.8v4.4h-4.4"/>',
    "search": '<circle cx="10.5" cy="10.5" r="6"/><path d="M15 15l5.5 5.5"/>',
    "shield": '<path d="M12 3l7 2.8v5.7c0 4.4-2.9 7.4-7 9.5-4.1-2.1-7-5.1-7-9.5V5.8z"/>'
              '<path d="M9.2 11.8l2 2 3.6-3.9"/>',
    "warn": '<path d="M12 4l9 16H3z"/><path d="M12 10v4.2M12 17.2v.2"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "cross": '<path d="M6 6l12 12M18 6L6 18"/>',
    "info": '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5M12 8v.2"/>',
    "db": '<ellipse cx="12" cy="6" rx="7.5" ry="3"/><path d="M4.5 6v12c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3V6"/>'
          '<path d="M4.5 12c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3"/>',
    "copy": '<rect x="8.5" y="8.5" width="11" height="11" rx="2"/>'
            '<path d="M15.5 5.5v-1a2 2 0 0 0-2-2h-9a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h1"/>',
    "livetrack": '<circle cx="12" cy="12" r="8.5"/>'
                 '<circle cx="12" cy="12" r="3.2" fill="$C" stroke="none"/>'
                 '<path d="M12 1.5v3.5M12 19v3.5M1.5 12H5M19 12h3.5"/>',
}


@lru_cache(maxsize=None)
def icon(name: str, color: str = "#d8dee8", size: int = 20) -> QIcon:
    body = _BODIES.get(name, _BODIES["info"])
    svg = _SVG_WRAP.replace("$BODY", body).replace("$C", color)
    renderer = QSvgRenderer(svg.encode("utf-8"))
    dpr = 3
    image = QImage(size * dpr, size * dpr, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    renderer.render(painter)
    painter.end()
    pixmap = QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(float(dpr))
    return QIcon(pixmap)
