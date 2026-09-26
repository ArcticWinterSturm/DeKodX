"""Render offscreen screenshots of DeKodX pages into docs/ for the README."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from PyQt6.QtCore import QEventLoop, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

import make_fixture  # noqa: E402

from dex.ui import theme  # noqa: E402
from dex.ui.main_window import MainWindow  # noqa: E402

OUT = ROOT / "docs"


def grab(window, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    pixmap = window.grab()
    path = OUT / name
    pixmap.save(str(path))
    print("saved", path)


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="dekodx_shot_"))
    source = make_fixture.build(tmp / "home" / "Alice" / ".codex", big=False)
    output = tmp / "report.md"

    app = QApplication(sys.argv)
    theme.apply(app)
    window = MainWindow(initial_source=str(source))
    window.resize(1280, 840)
    window.show()
    app.processEvents()

    loop = QEventLoop()
    QTimer.singleShot(1800, loop.quit)
    loop.exec()
    grab(window, "ui_dashboard.png")

    window.show_page(1)
    window.recover.source_row.setText(str(source))
    window.recover.output_row.setText(str(output))
    for msg, level in (("Scanning /tmp/…/home/Alice/.codex", "info"),
                       ("Found 5 index entries, 3 active + 1 archived transcripts", "info"),
                       ("State DB: 4 threads, 2 dynamic tools, 1 goals, 1 jobs, 1 spawn edges", "info"),
                       ("Redaction level: standard (12 hard secrets registered)", "info"),
                       ("Parsing session 1/5: 019f2960-…", "info"),
                       ("  transcript missing for 019eaaaa-…", "warn"),
                       ("Parsing session 5/5: 019f2963-…", "info"),
                       ("Loaded 1000 log entries", "info"),
                       ("Report written: " + str(output) + " (84,622 chars)", "ok")):
        window.recover.log(msg, level)
    window.recover.progress.update(0.67, "Parsing session 4/5…")
    app.processEvents()
    grab(window, "ui_recover.png")

    window.begin_recovery({
        "source": str(source),
        "output": str(output),
        "options": window.recover.collect_options(),
    })
    done = QEventLoop()
    window.worker.finished.connect(done.quit)
    QTimer.singleShot(30000, done.quit)
    done.exec()
    app.processEvents()
    window.show_page(2)
    app.processEvents()
    grab(window, "ui_report.png")


if __name__ == "__main__":
    main()
