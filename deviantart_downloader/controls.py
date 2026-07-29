"""Live keyboard controls during a download: pause, resume, quit.

While a job runs (listing and downloading) a background thread reads single
keypresses without waiting for Enter and toggles the shared run state in
constants:

  * p -> pause      (workers block on RESUME)
  * r -> resume     (workers run again)
  * q -> quit       (like Ctrl+C: stop and clean up)

The available keys, the current state and what the run is working on are shown
on a status line pinned to the bottom of the terminal: stdout is wrapped so
every line the program prints scrolls above that footer, which is redrawn
underneath and updated the moment a key is pressed or progress moves. That
makes the footer the progress channel for -q, which drops the scrolling
commentary but should still show a sign of life.

This needs a real terminal; when stdout is not a TTY (piped, tests, a non-POSIX
platform) the controls and the footer are inactive and output is unchanged.
Ctrl+C keeps working either way: cbreak mode leaves the terminal's signals on.
"""

import shutil
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

_CLEAR_LINE = "\r\x1b[2K"                  # carriage return + erase whole line


_PROGRESS = ""                             # what the run is working on right now


def set_progress(text: str) -> None:
    """Report what the run is working on, in the pinned footer.

    The footer is where progress belongs under -q: the scrolling commentary is
    what a quiet run drops, but a long sync should still show a sign of life.
    Feeding it unconditionally keeps one mechanism rather than two, so a normal
    run gets a stable counter under its output at no extra cost.

    Whether there is a footer at all is already recorded by stdout being the
    wrapper that draws it, so there is no second registry to keep in step.
    """
    global _PROGRESS
    _PROGRESS = text
    writer = sys.stdout
    if isinstance(writer, _FooterWriter):
        writer.set_footer(footer_text())


_GAP = 2               # blanks kept between the progress and the keys
_MIN_PROGRESS = 12     # below this the split is not worth the columns it costs


def _trim(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:max(limit - 1, 0)] + "…"


def footer_text(width: int | None = None) -> str:
    """The status line to pin at the bottom, reflecting the current state.

    The keys sit flush against the right edge and the progress grows from the
    left, so the hint stays where the eye left it instead of sliding about as
    work names change length. It is the progress that gets trimmed when the two
    would collide, since that is the part you can afford to read partially.

    The whole line is kept a column short of the edge: writing into the last one
    makes some terminals wrap, and a wrapped footer outlives the single line the
    writer erases to redraw it.
    """
    if CANCEL.is_set():
        state, keys = "[quitting...]", ""
    elif not RESUME.is_set():
        state, keys = "[PAUSED]", "keys: [r] resume  [q] quit"
    else:
        state, keys = "[running]", "keys: [p] pause  [r] resume  [q] quit"
    columns = (shutil.get_terminal_size(fallback=(80, 24)).columns
               if width is None else width)
    limit = max(columns - 1, 0)
    left = "  ".join(part for part in (state, _PROGRESS) if part)
    if not keys:
        return _trim(left, limit)
    room = limit - len(keys) - _GAP
    if room < _MIN_PROGRESS:
        # Too narrow to hold both apart; run them together and cut the tail.
        return _trim(f"{left}  {keys}", limit)
    return _trim(left, room).ljust(room) + " " * _GAP + keys


def apply_key(ch: str) -> bool:
    """Apply one keypress to the shared run state; return True if it changed.

    Pure but for the shared events, so it can be tested without a terminal.
    """
    ch = ch.lower()
    if ch == "q":
        if not CANCEL.is_set():
            CANCEL.set()
            RESUME.set()                  # wake any paused workers so they abort
            return True
    elif ch == "p":
        if RESUME.is_set() and not CANCEL.is_set():
            RESUME.clear()
            return True
    elif ch == "r":
        if not RESUME.is_set() and not CANCEL.is_set():
            RESUME.set()
            return True
    return False


class _FooterWriter:
    """stdout wrapper that keeps a status line pinned below the output.

    Each written line clears the footer, prints the line, then redraws the
    footer on the new last line; set_footer refreshes it in place. A lock keeps
    the escape sequences intact when worker threads print at the same time.
    """

    def __init__(self, stream):
        self._stream = stream
        self._buffer = ""
        self._footer = ""
        self._lock = threading.RLock()

    def set_footer(self, text: str):
        with self._lock:
            self._footer = text
            self._stream.write(_CLEAR_LINE + text)
            self._stream.flush()

    def clear_footer(self):
        with self._lock:
            self._stream.write(_CLEAR_LINE)
            self._stream.flush()

    def write(self, s: str) -> int:
        with self._lock:
            self._buffer += s
            while "\n" in self._buffer:
                line, self._buffer = self._buffer.split("\n", 1)
                self._stream.write(_CLEAR_LINE + line + "\n" + self._footer)
            self._stream.flush()
        return len(s)

    def flush(self):
        self._stream.flush()

    def __getattr__(self, name):
        # Delegate everything else (isatty, fileno, encoding, ...) to the stream.
        return getattr(self._stream, name)


class KeyboardControls:
    """Context manager that reads keypresses and pins a status footer."""

    def __init__(self, stream=None):
        self.stream = stream or sys.stdin
        self.active = False
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._saved = None
        self._fd = None
        self._writer: _FooterWriter | None = None
        self._orig_stdout = None

    def __enter__(self):
        if not (_HAS_TERMIOS and self._isatty()):
            return self                   # inactive: no terminal to drive
        self._fd = self.stream.fileno()
        try:
            self._saved = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)       # single keys, no echo; signals stay on
        except (termios.error, ValueError, OSError):
            self._saved = None
            return self
        self.active = True
        self._orig_stdout = sys.stdout
        self._writer = _FooterWriter(sys.stdout)
        sys.stdout = self._writer
        self._writer.set_footer(footer_text())
        self._thread = threading.Thread(target=self._listen, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        global _PROGRESS
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
        if self.active:
            self._writer.clear_footer()
            sys.stdout = self._orig_stdout
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)
        self.active = False
        _PROGRESS = ""       # never linger into the next user's footer
        return False

    def _isatty(self) -> bool:
        try:
            return self.stream.isatty()
        except (ValueError, OSError):
            return False

    def _refresh(self):
        if self._writer is not None:
            self._writer.set_footer(footer_text())

    def _listen(self):
        while not self._stop.is_set() and not CANCEL.is_set():
            ready, _, _ = select.select([self.stream], [], [], 0.2)
            if not ready:
                continue
            ch = self.stream.read(1)
            if not ch:
                continue
            if apply_key(ch):
                self._refresh()
            if ch.lower() == "q":
                break
