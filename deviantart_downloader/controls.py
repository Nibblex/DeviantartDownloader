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

A rate-limit wait gets a second line above it, counting down, rather than the
line per 429 it used to print -- several workers, several attempts, per user
adds up to hundreds. The countdown ticks from the key-reading loop, since every
worker is blocked while it runs and nobody else would redraw it.

This needs a real terminal; when stdout is not a TTY (piped, tests, a non-POSIX
platform) the controls and the footer are inactive and output is unchanged.
Ctrl+C keeps working either way: cbreak mode leaves the terminal's signals on.
"""

import shutil
import sys
import threading
import time

from .constants import CANCEL, RESUME

try:
    import select
    import termios
    import tty
    _HAS_TERMIOS = True
except ImportError:                       # non-POSIX platform
    _HAS_TERMIOS = False

_CLEAR_LINE = "\r\x1b[2K"                  # carriage return + erase whole line
_UP = "\x1b[1A"                            # one line up, to reach a block above


_PROGRESS = ""                             # what the run is working on right now
_HOLD_UNTIL = 0.0                          # monotonic deadline of a rate-limit wait
_HOLD_HITS = 0                             # 429s since the last request got through


def set_hold(seconds: float) -> None:
    """Report that the run is waiting out a 429, on either route.

    This replaces a line per 429, which drowned the output: several workers each
    hitting the limit, several times, per user. The footer says the same thing in
    one place and counts down, which also shows the run is waiting rather than
    hung. Off a terminal there is no footer, so the start of a stall is announced
    once -- only when hits is 1, so the escalations that follow stay quiet.
    """
    global _HOLD_UNTIL, _HOLD_HITS
    # A stall starts when nothing was being waited out; the 429s that pile on
    # while it runs extend it rather than beginning another one. Counted here
    # rather than off the backoff ladder, which an explicit Retry-After never
    # climbs -- that would leave a stall announcing itself never.
    starting = hold_seconds() <= 0
    _HOLD_HITS = 1 if starting else _HOLD_HITS + 1
    _HOLD_UNTIL = time.monotonic() + seconds
    if not _redraw() and starting:
        print(f"  Rate limit reached; holding off. "
              f"Waiting {seconds:.0f}s, and longer if it persists...")


def clear_hold() -> None:
    """A request got through: the wait is over."""
    global _HOLD_UNTIL, _HOLD_HITS
    if not _HOLD_UNTIL:
        return
    _HOLD_UNTIL, _HOLD_HITS = 0.0, 0
    _redraw()


def hold_seconds() -> float:
    """Seconds left on the rate-limit wait, 0 when there is none."""
    return max(_HOLD_UNTIL - time.monotonic(), 0.0) if _HOLD_UNTIL else 0.0


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
    _redraw()


_GAP = 2               # blanks kept between the two halves of a line
_MIN_LEFT = 12         # below this the split is not worth the columns it costs


def _redraw() -> bool:
    """Repaint the pinned block; False when there is no terminal to paint on.

    The one place that decides whether a footer exists and what it holds. Handing
    the writer lines from anywhere else is how the status line once got painted
    one character per row: a str is iterable too.
    """
    writer = sys.stdout
    if not isinstance(writer, _FooterWriter):
        return False
    writer.set_footer(footer_lines())
    return True


def _trim(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:max(limit - 1, 0)] + "…"


def _limit(width: int | None) -> int:
    """Columns a footer line may use.

    One short of the edge: writing into the last one makes some terminals wrap,
    and a wrapped line outlives the single row the writer erases to redraw it.
    """
    columns = (shutil.get_terminal_size(fallback=(80, 24)).columns
               if width is None else width)
    return max(columns - 1, 0)


def _two_column(left: str, right: str, limit: int) -> str:
    """`left` growing from the margin, `right` anchored against the far edge.

    The right-hand half is the fixed reference the eye returns to, so it is the
    left that gives way when the two would collide -- that half you can read
    partially and still follow. Too narrow to hold them apart and they simply
    run together, cut at the end.
    """
    if not right:
        return _trim(left, limit)
    room = limit - len(right) - _GAP
    if room < _MIN_LEFT:
        return _trim(f"{left}  {right}", limit)
    return _trim(left, room).ljust(room) + " " * _GAP + right


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
    left = "  ".join(part for part in (state, _PROGRESS) if part)
    return _two_column(left, keys, _limit(width))


def hold_line(width: int | None = None) -> str:
    """The rate-limit line, laid out like the status line under it."""
    return _two_column(f"[rate limit]  resuming in {hold_seconds():.0f}s",
                       f"429s so far: {_HOLD_HITS}", _limit(width))


def footer_lines(width: int | None = None) -> list[str]:
    """The block to pin at the bottom: the status line, and the wait above it.

    The wait gets its own line rather than a corner of the status line, which is
    already full: the progress fills the middle and the keys are anchored right.
    It sits above so the keys stay on the last line, where they have been.
    """
    lines = [footer_text(width)]
    if hold_seconds() > 0:
        lines.insert(0, hold_line(width))
    return lines


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
    """stdout wrapper that keeps a status block pinned below the output.

    Each written line erases the block, prints the line, then redraws the block
    underneath; set_footer refreshes it in place. The block is one line most of
    the time and two while a rate limit is being waited out, so the writer keeps
    track of how many it drew: erasing the wrong number would leave a stale line
    on screen or eat one of the program's own. A lock keeps the escape sequences
    intact when worker threads print at the same time.
    """

    def __init__(self, stream):
        self._stream = stream
        self._buffer = ""
        self._footer: list[str] = []
        self._lock = threading.RLock()

    def _drawn(self) -> str:
        return "\n".join(self._footer)

    def _erase(self) -> str:
        """Sequence that clears the drawn block and leaves the cursor at its top."""
        return _CLEAR_LINE + (_UP + _CLEAR_LINE) * max(len(self._footer) - 1, 0)

    def set_footer(self, lines: list[str]):
        with self._lock:
            out = self._erase()
            self._footer = list(lines)
            self._stream.write(out + self._drawn())
            self._stream.flush()

    def clear_footer(self):
        with self._lock:
            self._stream.write(self._erase())
            self._footer = []
            self._stream.flush()

    def write(self, s: str) -> int:
        with self._lock:
            self._buffer += s
            while "\n" in self._buffer:
                line, self._buffer = self._buffer.split("\n", 1)
                self._stream.write(self._erase() + line + "\n" + self._drawn())
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
        _redraw()
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

    def _listen(self):
        shown = None
        while not self._stop.is_set() and not CANCEL.is_set():
            ready, _, _ = select.select([self.stream], [], [], 0.2)
            if not ready:
                # Nobody redraws the footer while every worker is blocked on a
                # rate-limit wait, so the countdown ticks from here -- once per
                # second it changes by, not once per poll.
                left = round(hold_seconds())
                if left != shown and (left or shown):
                    shown = left
                    _redraw()
                continue
            shown = None
            ch = self.stream.read(1)
            if not ch:
                continue
            if apply_key(ch):
                _redraw()
            if ch.lower() == "q":
                break
