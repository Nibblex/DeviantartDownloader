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


def wait_if_paused() -> None:
    """Block a worker thread while the run is paused.

    Polls rather than waiting outright so a cancel (Ctrl+C or 'q') always wakes
    the thread within a fraction of a second, even if it was paused: the caller
    checks CANCEL right after and aborts.
    """
    while not RESUME.is_set() and not CANCEL.is_set():
        RESUME.wait(0.2)
