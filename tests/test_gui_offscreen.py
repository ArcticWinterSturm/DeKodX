"""Offscreen smoke test for the Qt6 UI (no display needed).

Run:  QT_QPA_PLATFORM=offscreen python3 tests/test_gui_offscreen.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from PyQt6.QtCore import QEventLoop, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

import make_fixture  # noqa: E402

from dex.ui import theme  # noqa: E402
from dex.ui.main_window import MainWindow  # noqa: E402

FAIL = 0


def check(label: str, condition: bool) -> None:
    global FAIL
    print(("  ok   " if condition else "  FAIL ") + label)
    if not condition:
        FAIL += 1


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="dekodx_gui_"))
    source = make_fixture.build(tmp / "home" / "Alice" / ".codex", big=False)
    output = tmp / "report.md"

    app = QApplication(sys.argv)
    theme.apply(app)
    window = MainWindow(initial_source=str(source))
    window.show()
    check("window constructed and shown", window.isVisible())
    check("six pages stacked", window.stack.count() == 6)

    # navigate every page
    for index in range(6):
        window.show_page(index)
        check(f"page {index} active", window.stack.currentIndex() == index)

    # dashboard scan (async worker) - pump events until inventory fills
    window.show_page(0)
    loop = QEventLoop()
    QTimer.singleShot(2500, loop.quit)
    loop.exec()
    check("dashboard inventory populated", window.dashboard.inventory.count() > 3)

    # run a full recovery through the worker thread
    window.show_page(1)
    window.recover.source_row.setText(str(source))
    window.recover.output_row.setText(str(output))
    window.begin_recovery({
        "source": str(source),
        "output": str(output),
        "options": window.recover.collect_options(),
    })
    check("worker running", window.worker is not None and window.worker.isRunning())
    done = QEventLoop()
    window.worker.finished.connect(done.quit)
    QTimer.singleShot(30000, done.quit)
    done.exec()
    settle = QEventLoop()
    QTimer.singleShot(600, settle.quit)
    settle.exec()
    app.processEvents()
    check("report file written", output.exists())
    check("console logged progress", window.recover.console.toPlainText().count("\n") > 3)
    check("report page loaded preview", len(window.report.preview.toPlainText()) > 500)
    check("progress reached 100%", window.recover.progress.bar.value() == 1000)

    # cancel path: start again and cancel immediately
    window.show_page(1)
    window.begin_recovery({
        "source": str(source),
        "output": str(tmp / "cancelled.md"),
        "options": window.recover.collect_options(),
    })
    window.cancel_recovery()
    done2 = QEventLoop()
    window.worker.finished.connect(done2.quit)
    QTimer.singleShot(30000, done2.quit)
    done2.exec()
    app.processEvents()
    check("cancelled run did not write report", not (tmp / "cancelled.md").exists())

    # -- Chat & Work page: run a chatwork scan through its worker ------------ #
    # (chatwork is stack index 3; index 2 is Live Tracking)
    import make_chatwork_fixture as cfx
    cw_paths = cfx.build_all(tmp / "chatwork")
    window.show_page(3)
    window.chatwork.codex_row.setText(str(cw_paths["codex"]))
    window.chatwork.output_row.setText(str(tmp / "chatwork_report.md"))
    window.chatwork.export_list.addItem(str(cw_paths["export"]))
    window.chatwork.chk_browser.setChecked(False)      # fixture browsers not on real LOCALAPPDATA
    window.begin_chatwork({
        "codex_home": str(cw_paths["codex"]),
        "exports": [str(cw_paths["export"])],
        "output": str(tmp / "chatwork_report.md"),
        "scan_browser": False,
        "scan_rollouts": True,
        "redact": True,
    })
    check("chatwork worker running",
          window._chatwork_worker is not None and window._chatwork_worker.isRunning())
    done3 = QEventLoop()
    window._chatwork_worker.finished.connect(done3.quit)
    QTimer.singleShot(30000, done3.quit)
    done3.exec()
    app.processEvents()
    check("chatwork report written", (tmp / "chatwork_report.md").exists())
    cw_md = (tmp / "chatwork_report.md").read_text(encoding="utf-8")
    check("chatwork report has catalog section", "## Thread Catalog" in cw_md)
    check("chatwork report has export section", "## Export Archives" in cw_md)
    check("chatwork console logged", "Chat & Work report complete" in
          window.chatwork.console.toPlainText())

    # -- Live Tracking: audit-mode smoke test against the fixture ------------- #
    # The engine must run off the GUI thread, arm its cursors without replaying
    # old rows, and write the session report on stop.
    window.show_page(2)
    window.livetrack._codex_root_override = source
    window.livetrack.out_row.setText(str(tmp / "streamed"))
    window.livetrack.audit_btn.click()
    check("trap worker running",
          window.livetrack._worker is not None and window.livetrack._worker.isRunning())
    loop2 = QEventLoop()
    QTimer.singleShot(2500, loop2.quit)
    loop2.exec()
    lt_console = window.livetrack.console.toPlainText()
    check("trap engine started", "engine start" in lt_console)
    check("trap attached fixture rollouts", "attached" in lt_console and "rollout" in lt_console)
    check("trap usage baseline primed (no replay of all threads)",
          "usage baseline primed" in lt_console)
    # stop is a bounded join on the GUI thread; must not hang
    window.livetrack.stop_btn.click()
    check("trap worker stopped",
          window.livetrack._worker is None)
    reports = list((tmp / "streamed").glob("trap_session_*.md"))
    check("trap session report written", len(reports) == 1)
    check("trap console logged stop", "engine stopped" in
          window.livetrack.console.toPlainText())

    window.close()
    print("\nGUI smoke test:", "PASSED" if FAIL == 0 else f"{FAIL} FAILURES")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
