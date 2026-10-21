"""Private, bounded diagnostic logs for the zterm application.

Logs contain event names and Python stack locations. Callers must not log
terminal text, transfer contents, credentials, or environment variables.
"""

import faulthandler
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import signal
import sys
import threading
import time
import traceback


LOGGER = logging.getLogger("zterm")
_LOG_PATH = None
_CRASH_FILE = None
_WATCHDOG = None
_GLIB_HANDLERS = []


def _private_open(path, flags):
    return os.open(path, flags | getattr(os, "O_NOFOLLOW", 0), 0o600)


class _PrivateRotatingFileHandler(RotatingFileHandler):
    def _open(self):
        return open(
            self.baseFilename,
            self.mode,
            encoding=self.encoding,
            errors=self.errors,
            opener=_private_open,
        )


def _log_exception(kind, exc_type, exc_traceback):
    # Exception messages and source lines can contain passwords or user data.
    frames = traceback.extract_tb(exc_traceback)
    locations = " -> ".join(
        f"{frame.filename}:{frame.lineno} in {frame.name}" for frame in frames[-32:]
    )
    LOGGER.error("%s: %s at %s", kind, exc_type.__name__, locations)


def _thread_exception(args):
    _log_exception("Unhandled thread exception", args.exc_type, args.exc_traceback)


def setup_logging():
    """Initialize diagnostics once and return the primary log path.

    The SIGUSR1 thread dump and fatal interpreter tracebacks are written to
    crash.log in the same private directory. The application log is rotated.
    """
    global _LOG_PATH, _CRASH_FILE
    if _LOG_PATH is not None:
        return _LOG_PATH

    state_home = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
    log_dir = state_home / "zterm"
    log_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if log_dir.is_symlink():
        raise OSError("Diagnostic directory must not be a symbolic link")
    log_dir.chmod(0o700)
    log_path = log_dir / "zterm.log"
    level_name = os.environ.get("ZTERM_LOG_LEVEL", "INFO").upper()
    level = logging.DEBUG if level_name == "DEBUG" else logging.INFO

    handler = _PrivateRotatingFileHandler(
        log_path,
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    os.fchmod(handler.stream.fileno(), 0o600)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOGGER.addHandler(handler)
    LOGGER.setLevel(level)
    LOGGER.propagate = False

    crash_path = log_dir / "crash.log"
    # Bound data left by previous runs; faulthandler needs a stable open FD.
    _CRASH_FILE = open(crash_path, "a", encoding="utf-8", opener=_private_open)
    os.fchmod(_CRASH_FILE.fileno(), 0o600)
    if os.fstat(_CRASH_FILE.fileno()).st_size > 2 * 1024 * 1024:
        os.ftruncate(_CRASH_FILE.fileno(), 0)
    faulthandler.enable(file=_CRASH_FILE, all_threads=True)
    if hasattr(signal, "SIGUSR1"):
        faulthandler.register(signal.SIGUSR1, file=_CRASH_FILE, all_threads=True, chain=False)

    sys.excepthook = lambda exc_type, exc_value, exc_tb: _log_exception(
        "Unhandled exception", exc_type, exc_tb
    )
    threading.excepthook = _thread_exception
    _LOG_PATH = log_path
    LOGGER.info("Diagnostics initialized; crash dumps: %s", crash_path)
    return log_path


def get_log_path():
    """Return the active diagnostic log path, or None before initialization."""
    return _LOG_PATH


def capture_toolkit_warnings():
    """Capture GTK/VTE warnings as well as Python exceptions."""
    from gi.repository import GLib

    if _GLIB_HANDLERS:
        return

    def reported(domain, level, message, *_):
        # Toolkit messages may interpolate terminal data or user input. Keep
        # the origin and call locations, but never copy their message text.
        frames = traceback.extract_stack(limit=12)
        locations = " -> ".join(
            f"{frame.filename}:{frame.lineno} in {frame.name}" for frame in frames
        )
        LOGGER.warning("Toolkit domain=%s level=%s at %s", domain, int(level), locations)

    levels = (GLib.LogLevelFlags.LEVEL_WARNING | GLib.LogLevelFlags.LEVEL_CRITICAL |
              GLib.LogLevelFlags.LEVEL_ERROR | GLib.LogLevelFlags.FLAG_FATAL |
              GLib.LogLevelFlags.FLAG_RECURSION)
    for domain in ("Gtk", "Gdk", "Vte", "GLib", "GLib-GObject"):
        _GLIB_HANDLERS.append((domain, GLib.log_set_handler(domain, levels, reported, None)))


class MainLoopWatchdog:
    """Report the first prolonged gap in main-loop heartbeats and recovery."""

    def __init__(self, timeout=10.0):
        self.timeout = timeout
        self._last_heartbeat = time.monotonic()
        self._stalled = False
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="zterm-mainloop-watchdog", daemon=True
        )

    def start(self):
        self._thread.start()
        return self

    def mark_heartbeat(self):
        """Call from the GLib main loop; True keeps a GLib timer active."""
        with self._lock:
            recovered = self._stalled
            self._last_heartbeat = time.monotonic()
            self._stalled = False
        if recovered:
            LOGGER.info("Main loop recovered")
        return True

    def stop(self):
        self._stop_event.set()
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=1)

    def _run(self):
        while not self._stop_event.wait(min(self.timeout / 2, 1.0)):
            with self._lock:
                stalled = time.monotonic() - self._last_heartbeat >= self.timeout
                report = stalled and not self._stalled
                if report:
                    self._stalled = True
            if report:
                LOGGER.error("Main loop unresponsive for at least %.1f seconds", self.timeout)
                self._log_stacks()

    @staticmethod
    def _log_stacks():
        for thread_id, frame in list(sys._current_frames().items())[:64]:
            locations = traceback.extract_stack(frame)[-50:]
            stack = " -> ".join(
                f"{entry.filename}:{entry.lineno} in {entry.name}" for entry in locations
            )
            LOGGER.error("Thread %s stack: %s", thread_id, stack)


def start_mainloop_watchdog(timeout=10.0):
    """Start a watchdog; arrange a GLib heartbeat separately in the caller."""
    global _WATCHDOG
    if (_WATCHDOG is None or _WATCHDOG._stop_event.is_set()
            or not _WATCHDOG._thread.is_alive()):
        _WATCHDOG = MainLoopWatchdog(timeout).start()
    return _WATCHDOG
