"""
Centralised logging for Captain Jack.

On import, call setup_logging() once (main.py does this at boot).
After that, anywhere in the app:

    from core.logger import get_logger
    log = get_logger(__name__)
    log.info("...")
    log.error("...", exc_info=True)

Log files are written to  <project>/logs/captain_jack_YYYY-MM-DD.log
Daily rotation, 14 days of history kept automatically.
stdout/stderr are tee'd into the same file so bare print() calls
and library warnings are also captured.
"""

import logging
import sys
import traceback
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


def _logs_dir() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).resolve().parent.parent
    d = base / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


_FMT = "%(asctime)s  %(levelname)-8s  %(name)s  —  %(message)s"
_DATE = "%Y-%m-%d %H:%M:%S"


class _TeeStream:
    """Writes to both the original stream and the log file."""

    def __init__(self, original, logger: logging.Logger, level: int):
        self._orig  = original
        self._log   = logger
        self._level = level
        self._buf   = ""

    def write(self, text: str):
        self._orig.write(text)
        self._orig.flush()
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.rstrip()
            if line:
                self._log.log(self._level, line)

    def flush(self):
        self._orig.flush()

    def fileno(self):
        return self._orig.fileno()

    def isatty(self):
        try:
            return self._orig.isatty()
        except Exception:
            return False


_initialised = False


def setup_logging(level: int = logging.DEBUG) -> None:
    global _initialised
    if _initialised:
        return
    _initialised = True

    logs_dir  = _logs_dir()
    today     = datetime.now().strftime("%Y-%m-%d")
    log_file  = logs_dir / f"captain_jack_{today}.log"

    root = logging.getLogger()
    root.setLevel(level)

    # Remove any existing handlers so we don't double-log
    root.handlers.clear()

    # --- File handler: daily rotation, keep 14 days ---
    fh = TimedRotatingFileHandler(
        log_file,
        when="midnight",
        backupCount=14,
        encoding="utf-8",
        utc=False,
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(_FMT, datefmt=_DATE))
    fh.suffix = "%Y-%m-%d"
    root.addHandler(fh)

    # --- Console handler: INFO and above ---
    ch = logging.StreamHandler(sys.__stdout__)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter(_FMT, datefmt=_DATE))
    root.addHandler(ch)

    # --- Tee stdout/stderr so bare print() calls land in the log too ---
    _stdout_logger = logging.getLogger("stdout")
    _stderr_logger = logging.getLogger("stderr")
    sys.stdout = _TeeStream(sys.__stdout__, _stdout_logger, logging.INFO)
    sys.stderr = _TeeStream(sys.__stderr__, _stderr_logger, logging.WARNING)

    # --- Catch any unhandled exception and write full traceback ---
    def _exc_handler(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logging.getLogger("uncaught").critical(
            "Unhandled exception",
            exc_info=(exc_type, exc_value, exc_tb),
        )

    sys.excepthook = _exc_handler

    logging.getLogger("captain_jack").info(
        "=== Captain Jack started — logs → %s ===", log_file
    )


def get_logger(name: str = "captain_jack") -> logging.Logger:
    return logging.getLogger(name)
