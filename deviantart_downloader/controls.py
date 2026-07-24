"""Live keyboard controls during a download: pause, resume, quit.

While files are downloading a background thread reads single keypresses
(without waiting for Enter) and toggles the shared run state in constants:

  * p -> pause      (workers block on RESUME)
  * r -> resume     (workers run again)
  * q -> quit       (like Ctrl+C: stop and clean up)

This needs a real terminal; when stdin is not a TTY (piped input, tests, a
non-POSIX platform) the controls are simply inactive and downloads behave
exactly as before. Ctrl+C keeps working either way: cbreak mode leaves the
terminal's signal handling on.
"""

import sys
import threading

from .constants import CANCEL, RESUME

try:
    import select
    import termios
    import tty
    _HAS_TERMIOS = True
except ImportError:                       # non-POSIX platform
    _HAS_TERMIOS = False

HINT = "Controls: [p] pause  [r] resume  [q] quit"


def apply_key(ch: str) -> str | None:
    """Apply one keypress to the shared run state; return a line to show, if any.

    Pure but for the shared events, so it can be tested without a terminal.
    """
    ch = ch.lower()
    if ch == "q":
        CANCEL.set()
        RESUME.set()                      # wake any paused workers so they abort
        return "\nQuitting: stopping downloads and cleaning up..."
    if ch == "p":
        if RESUME.is_set() and not CANCEL.is_set():
            RESUME.clear()
            return "\n-- Paused. Press [r] to resume, [q] to quit. --"
        return None
    if ch == "r":
        if not RESUME.is_set():
            RESUME.set()
            return "-- Resumed. --"
        return None
    return None


class KeyboardControls:
    """Context manager that reads keypresses while its body runs downloads."""

    def __init__(self, stream=None):
        self.stream = stream or sys.stdin
        self.active = False
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._saved = None
        self._fd = None

    def __enter__(self):
        if not (_HAS_TERMIOS and self._isatty()):
            return self                   # inactive: no terminal to read from
        self._fd = self.stream.fileno()
        try:
            self._saved = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)       # single keys, no echo; signals stay on
        except (termios.error, ValueError, OSError):
            self._saved = None
            return self
        self.active = True
        print(HINT, flush=True)
        self._thread = threading.Thread(target=self._listen, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
        if self._saved is not None:
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)
        self.active = False
        return False

    def _isatty(self) -> bool:
        try:
            return self.stream.isatty()
        except (ValueError, OSError):
            return False

    def _listen(self):
        while not self._stop.is_set() and not CANCEL.is_set():
            ready, _, _ = select.select([self.stream], [], [], 0.2)
            if not ready:
                continue
            ch = self.stream.read(1)
            if not ch:
                continue
            message = apply_key(ch)
            if message:
                print(message, flush=True)
            if ch.lower() == "q":
                break
