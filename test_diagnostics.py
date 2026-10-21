import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

import diagnostics


class DiagnosticsTests(unittest.TestCase):
    def test_private_logs_rotation_and_exception_hooks(self):
        script = """
import logging
import os
from pathlib import Path
import stat
import signal
import ctypes
import sys
import threading
import diagnostics

path = diagnostics.setup_logging()
assert path == Path(os.environ['XDG_STATE_HOME']) / 'zterm/zterm.log'
assert diagnostics.get_log_path() == path
assert diagnostics.setup_logging() == path
assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
assert stat.S_IMODE(path.stat().st_mode) == 0o600
assert stat.S_IMODE((path.parent / 'crash.log').stat().st_mode) == 0o600
assert diagnostics.LOGGER.level == logging.DEBUG
os.kill(os.getpid(), signal.SIGUSR1)
assert 'Current thread' in (path.parent / 'crash.log').read_text()
diagnostics.capture_toolkit_warnings()
glib = ctypes.CDLL('libglib-2.0.so.0')
glib.g_log.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p]
glib.g_log.restype = None
glib.g_log(b'Gtk', 16, b'diagnostic test warning secret=DO_NOT_LOG')
handler = diagnostics.LOGGER.handlers[0]
assert len(diagnostics.LOGGER.handlers) == 1
assert handler.maxBytes == 2 * 1024 * 1024
assert handler.backupCount == 3

def fail():
    raise ValueError('secret=DO_NOT_LOG')

try:
    fail()
except ValueError:
    exc_type, exc_value, exc_tb = sys.exc_info()
    sys.excepthook(exc_type, exc_value, exc_tb)
    threading.excepthook(type('Args', (), {
        'exc_type': exc_type, 'exc_value': exc_value, 'exc_traceback': exc_tb
    })())

handler.doRollover()
diagnostics.LOGGER.info('after rotation')
handler.flush()
assert stat.S_IMODE(path.stat().st_mode) == 0o600
assert stat.S_IMODE(path.with_name('zterm.log.1').stat().st_mode) == 0o600
combined = path.read_text() + path.with_name('zterm.log.1').read_text()
assert 'Unhandled exception: ValueError' in combined
assert 'Toolkit domain=Gtk' in combined and 'level=16' in combined
assert 'diagnostic test warning' not in combined
assert 'Unhandled thread exception: ValueError' in combined
assert 'secret=DO_NOT_LOG' not in combined
"""
        with tempfile.TemporaryDirectory() as state_home:
            env = dict(os.environ, XDG_STATE_HOME=state_home, ZTERM_LOG_LEVEL="DEBUG")
            result = subprocess.run(
                [sys.executable, "-c", script],
                env=env,
                cwd=Path(__file__).parent,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_watchdog_reports_once_per_stall_and_recovery(self):
        messages = []

        class RecordingHandler(logging.Handler):
            def emit(self, record):
                messages.append(record.getMessage())

        handler = RecordingHandler()
        old_level = diagnostics.LOGGER.level
        diagnostics.LOGGER.setLevel(logging.INFO)
        diagnostics.LOGGER.addHandler(handler)
        watchdog = diagnostics.MainLoopWatchdog(timeout=0.05).start()
        try:
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not any(
                "Main loop unresponsive" in message for message in messages
            ):
                time.sleep(0.01)
            self.assertEqual(
                sum("Main loop unresponsive" in message for message in messages), 1
            )
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not any(
                "stack:" in message for message in messages
            ):
                time.sleep(0.01)
            self.assertTrue(any("stack:" in message for message in messages))
            time.sleep(0.1)
            self.assertEqual(
                sum("Main loop unresponsive" in message for message in messages), 1
            )
            self.assertTrue(watchdog.mark_heartbeat())
            self.assertIn("Main loop recovered", messages)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and sum(
                "Main loop unresponsive" in message for message in messages
            ) < 2:
                time.sleep(0.01)
            self.assertEqual(
                sum("Main loop unresponsive" in message for message in messages), 2
            )
        finally:
            watchdog.stop()
            diagnostics.LOGGER.removeHandler(handler)
            diagnostics.LOGGER.setLevel(old_level)

    def test_watchdog_can_restart_after_application_shutdown(self):
        first = diagnostics.start_mainloop_watchdog()
        first.stop()
        second = diagnostics.start_mainloop_watchdog()
        try:
            self.assertIsNot(first, second)
            self.assertTrue(second._thread.is_alive())
        finally:
            second.stop()


if __name__ == "__main__":
    unittest.main(verbosity=2)
