"""Endpoints, limits and the flags every other module shares."""

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
