"""Endpoints, limits and the flags every other module shares."""

import os
import sys
import threading
from pathlib import Path


API_BASE = "https://www.deviantart.com/api/v1/oauth2"
TOKEN_URL = "https://www.deviantart.com/oauth2/token"
AUTH_URL = "https://www.deviantart.com/oauth2/authorize"
REDIRECT_PORT = 8721
REDIRECT_URI = f"http://127.0.0.1:{REDIRECT_PORT}/callback"
TOKEN_FILE = Path.home() / ".config" / "deviantart-downloader" / "token.json"
USER_AGENT = "da-gallery-downloader/1.0"
PAGE_LIMIT = 24  # maximum allowed by the API
# Requests per second the API route paces itself to, across every worker.
# Measured, not guessed; see "Staying under the API rate limit" in the README
# for the numbers and the reasoning. Tunable with DA_API_RATE (0 disables it).
API_RATE = 3.0

WEB_BASE = "https://www.deviantart.com"
GALLECTION_URL = f"{WEB_BASE}/_puppy/dashared/gallection/contents"
GALLECTION_FOLDERS_URL = f"{WEB_BASE}/_puppy/dashared/gallection/folders"
PROFILE_ABOUT_URL = f"{WEB_BASE}/_puppy/dauserprofile/init/about"
DEVIATION_INIT_URL = f"{WEB_BASE}/_puppy/dadeviation/init"
WEB_PAGE_LIMIT = 60
# The website's endpoints answer with a redirect to the app store or an empty
# payload unless the request looks like it comes from a browser.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

# One subfolder per route, so it is always obvious where a file came from.
WEB_SUBDIR = "web"
API_SUBDIR = "api"

# The formats a text work can be saved in (see --literature-format).
TEXT_FORMATS = ("txt", "html")

class CancelledByUser(RuntimeError):
    """A blocking wait was aborted because the user asked to stop (q / Ctrl+C).

    Raised by the API/website clients when a rate-limit wait is cut short by
    CANCEL, so the caller can exit cleanly instead of crashing with a traceback.
    """


# Set on Ctrl+C (or the 'q' key) so worker threads abort in-progress
# downloads promptly.
CANCEL = threading.Event()

# Cleared to pause the download workers, set to let them run (see controls.py).
# It starts set: downloads run unless the user presses 'p'.
RESUME = threading.Event()
RESUME.set()


# Cleared by -q/--quiet. Everything that reports progress goes through say()
# and falls silent; results, warnings, errors and prompts use print() directly
# and are shown either way, so a quiet run still says what happened and what
# went wrong. Shared like CANCEL/RESUME because every layer prints.
VERBOSE = threading.Event()
VERBOSE.set()


def say(*args, **kwargs) -> None:
    """print() for progress chatter: silent once -q/--quiet has been passed."""
    if VERBOSE.is_set():
        print(*args, **kwargs)


# Green for something found and usable, orange for something that is there but
# cannot be used as written. Orange is picked out of the 256-colour palette
# rather than taken from the eight basic ones, where the nearest is the yellow
# half the themes in existence render as brown.
GREEN = "\x1b[32m"
ORANGE = "\x1b[38;5;208m"
RESET = "\x1b[0m"


def _paint(text: str, colour: str, stream) -> str:
    """`text` in `colour`, or exactly as it came when nothing can show colour.

    Colour is never the only thing a line has to say for itself: every line
    that carries it also says in words what it means, so the text is worth no
    less plain. That is what makes it safe to drop, and it is dropped whenever
    the output is not going to a terminal -- redirected into a file or piped
    into another program the escapes would be noise sitting in the middle of
    the text. NO_COLOR (https://no-color.org) is obeyed for the same reason:
    somebody who has asked for no colour anywhere loses nothing by getting none
    here either.

    Both questions are asked per call rather than once at import, because
    neither answer holds still: stdout is swapped for the footer writer while a
    run is going (see controls.py), and a test may capture output long after
    this module was first imported. Which is also why the stream defaults here
    rather than in a signature, where sys.stdout would be bound at import.
    """
    out = stream or sys.stdout
    if os.environ.get("NO_COLOR"):
        return text
    try:
        if not out.isatty():
            return text
    except (ValueError, OSError):     # a stream already closed or detached
        return text
    return f"{colour}{text}{RESET}"


def green(text: str, stream=None) -> str:
    """Paint something that is in place and was understood."""
    return _paint(text, GREEN, stream)


def orange(text: str, stream=None) -> str:
    """Paint something that is there but not usable, and wants looking at."""
    return _paint(text, ORANGE, stream)


def sleep_or_cancel(seconds: float) -> None:
    """Wait out a throttle, but wake immediately on Ctrl+C or 'q'.

    Raises CancelledByUser rather than returning, so no caller can sleep
    through a cancel and then issue the request anyway.
    """
    if CANCEL.wait(max(seconds, 0.0)):
        raise CancelledByUser("Cancelled by the user")


def wait_if_paused() -> None:
    """Block a worker thread while the run is paused.

    Polls rather than waiting outright so a cancel (Ctrl+C or 'q') always wakes
    the thread within a fraction of a second, even if it was paused: the caller
    checks CANCEL right after and aborts.
    """
    while not RESUME.is_set() and not CANCEL.is_set():
        RESUME.wait(0.2)
