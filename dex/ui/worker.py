"""Background workers: recovery thread and generic function thread."""

from __future__ import annotations

import traceback

from PyQt6.QtCore import QThread, pyqtSignal

from ..core.models import RecoveryCancelled, RecoveryOptions
from ..core.recovery import run_recovery


class RecoveryWorker(QThread):
    log = pyqtSignal(str, str)            # message, level
    progress = pyqtSignal(float, str)     # 0..1, status label
    succeeded = pyqtSignal(str)           # output path
    failed = pyqtSignal(str)              # error message
    cancelled = pyqtSignal()

    def __init__(self, source: str, output: str, options: RecoveryOptions, parent=None):
        super().__init__(parent)
        self.source = source
        self.output = output
        self.options = options
        self._cancel = False
        self.result = None

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:  # noqa: D102 - QThread entry
        try:
            result = run_recovery(
                self.source,
                self.output,
                self.options,
                log=lambda msg, level="info": self.log.emit(msg, level),
                progress=lambda frac, label="": self.progress.emit(frac, label),
                cancel=lambda: self._cancel,
            )
            self.result = result
            if self._cancel:
                self.cancelled.emit()
            else:
                self.succeeded.emit(str(result.output_path))
        except RecoveryCancelled:
            self.cancelled.emit()
        except Exception as exc:  # surface everything to the UI log
            self.log.emit(traceback.format_exc(limit=6), "error")
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class FnWorker(QThread):
    """Runs a plain callable off the UI thread (dashboard scans, report loads)."""

    result = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, fn, *args, parent=None):
        super().__init__(parent)
        self.fn = fn
        self.args = args

    def run(self) -> None:  # noqa: D102 - QThread entry
        try:
            self.result.emit(self.fn(*self.args))
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class ChatWorkWorker(QThread):
    """Runs the Chat & Work archaeology scan with live log + progress."""

    log = pyqtSignal(str, str)            # message, level
    progress = pyqtSignal(float, str)     # 0..1, status label
    succeeded = pyqtSignal(str, dict)     # output path, stats
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, bundle: dict, parent=None):
        super().__init__(parent)
        self.bundle = bundle
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:  # noqa: D102 - QThread entry
        import traceback
        from .page_chatwork import chatwork_scan_fn
        try:
            path, stats = chatwork_scan_fn(
                self.bundle,
                log_fn=lambda msg, level="info": self.log.emit(msg, level),
                progress_fn=lambda frac, label="": self.progress.emit(frac, label),
                cancel_fn=lambda: self._cancel,
            )
            if self._cancel:
                self.cancelled.emit()
            else:
                self.succeeded.emit(path, stats)
        except Exception as exc:  # surface everything to the UI log
            self.log.emit(traceback.format_exc(limit=6), "error")
            self.failed.emit(f"{type(exc).__name__}: {exc}")
