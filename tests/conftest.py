"""Shared fixtures, fakes and factories.

Everything runs offline: HTTP traffic is simulated with scripted sessions and
responses, and the interactive login flow is not exercised.

Note on monkeypatching: patch a name in the module that *uses* it, which for
everything patched here is also the module that defines it (list_gallery calls
fetch_gallery from listing's own globals, process_deviation calls download_file
from downloads', and so on). Patching the package root would have no effect.
"""

import sys
import time

import pytest
import requests

from deviantart_downloader import cli
from deviantart_downloader.api import DeviantArtClient
from deviantart_downloader.constants import CANCEL

DEV_ID = "abcd1234-5678-90ab-cdef-1234567890ab"

WEB_ID = 1004952679
WEB_URL = f"https://www.deviantart.com/artist/art/Web-Art-{WEB_ID}"
BASE_URI = "https://images-wixmp-abc.wixmp.com/f/uuid/file.jpg"


@pytest.fixture(autouse=True)
def fresh_cancel():
    """Every module shares one CANCEL event, so clear it around each test."""
    CANCEL.clear()
    yield
    CANCEL.clear()


class OfflineSession:
    """Stand-in session that fails on use, not on construction."""

    def __init__(self):
        self.headers = {}

    def get(self, url, **kwargs):
        raise AssertionError(f"a test tried to reach the network: GET {url}")

    def post(self, url, **kwargs):
        raise AssertionError(f"a test tried to reach the network: POST {url}")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """A real request is a test bug, not a reason to skip."""
    monkeypatch.setattr(requests, "Session", OfflineSession)


# ---------------------------------------------------------------------------
# HTTP fakes
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, status_code=200, json_data=None, headers=None,
                 chunks=None, text=""):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.headers = headers or {}
        self._chunks = chunks if chunks is not None else [b"data"]
        self.text = text

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size):
        yield from self._chunks

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeSession:
    """Scripted requests.Session: queues of responses for get() and post()."""

    def __init__(self, get_responses=(), post_responses=()):
        self.headers = {}
        self.get_responses = list(get_responses)
        self.post_responses = list(post_responses)
        self.get_calls = []
        self.post_calls = []

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return self.get_responses.pop(0)

    def post(self, url, data=None, **kwargs):
        self.post_calls.append((url, data))
        return self.post_responses.pop(0)


def token_response():
    return FakeResponse(200, {"access_token": "tok", "refresh_token": "ref",
                              "expires_in": 3600})


def make_client(tmp_path, session, fresh_token=True):
    client = DeviantArtClient("id", "secret", token_file=tmp_path / "token.json")
    client.session = session
    if fresh_token:
        client._token_expiry = time.time() + 1000
    return client


class FakeClient:
    """Scripted API client: one dict per gallery page, in order."""

    def __init__(self, pages=None):
        self.pages = list(pages or [])
        self.calls = []
        self.session = FakeSession()

    def api_get(self, endpoint, params=None):
        self.calls.append((endpoint, params))
        return self.pages.pop(0)


class FakeWebClient:
    """Scripted website listing: one dict per gallery page, in order."""

    def __init__(self, pages=None):
        self.pages = list(pages or [])
        self.calls = []
        self.session = FakeSession()

    def gallery_page(self, username, offset, limit):
        self.calls.append((username, offset, limit))
        return self.pages.pop(0)


# ---------------------------------------------------------------------------
# Deviation factories
# ---------------------------------------------------------------------------

def make_dev(**overrides):
    """A deviation as the API returns it."""
    dev = {
        "deviationid": DEV_ID,
        "title": "My Art",
        "content": {"src": "https://example.com/pic.png"},
    }
    dev.update(overrides)
    return dev


def web_item(**overrides):
    """A listing entry in the website's own format."""
    item = {
        "deviationId": WEB_ID,
        "title": "Web Art",
        "url": WEB_URL,
        "type": "image",
        "isMature": False,
        "isBlocked": False,
        "isDownloadable": False,
        "blockReasons": [],
        "media": {
            "baseUri": BASE_URI,
            "prettyName": "web_art_by_artist_dxxxxxx",
            "token": ["tok0", "tok1"],
            "types": [
                {"t": "150", "r": 0, "c": "/v1/fit/w_150/<prettyName>-150.jpg"},
                {"t": "fullview", "r": 1, "w": 1000, "h": 800},
            ],
        },
    }
    item.update(overrides)
    return item


def blocked_web_item(**overrides):
    """A mature entry as a logged-out visitor sees it: blurred placeholder."""
    item = web_item(
        deviationId=222222222,
        title="Mature Art",
        url="https://www.deviantart.com/artist/art/Mature-Art-222222222",
        isMature=True,
        isBlocked=True,
        blockReasons=["mature_filter", "mature_loggedout"],
    )
    item["media"]["types"] = [
        {"t": "fullview", "r": 0, "c": "/v1/fill/w_564,h_484/<prettyName>-fullview.jpg"},
    ]
    item.update(overrides)
    return item


def csrf_page(token="csrf-123"):
    return FakeResponse(200, text=f'window.__CSRF_TOKEN__ = "{token}";')


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def manifest(tmp_path):
    from deviantart_downloader.manifest import DownloadManifest
    return DownloadManifest(tmp_path)


@pytest.fixture
def clean_cli_env(tmp_path, monkeypatch):
    """Isolated cwd, no .env pickup (the repo may have a real one) and no
    DA_* variables."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "load_dotenv", lambda path=None: None)
    for var in ("DA_CLIENT_ID", "DA_CLIENT_SECRET", "DA_WORKERS", "DA_UNBLUR",
                "DA_OUTPUT", "DA_API_ONLY"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def set_argv(monkeypatch, *args):
    """Build an argv. Tests opt into the website route explicitly with the
    marker "--web", so by default nothing reaches for the website."""
    argv = ["deviantart-downloader", *args]
    if "--api-only" not in argv and "--web" not in argv:
        argv.append("--api-only")
    monkeypatch.setattr(sys, "argv", [a for a in argv if a != "--web"])


def make_user_dir(root, username, marker="_downloaded.json", content="{}"):
    d = root / username
    d.mkdir(parents=True)
    (d / marker).write_text(content, encoding="utf-8")
    return d


def fake_download(session, url, dest, fallback=None):
    """Stand-in for download_file that just creates the file."""
    dest.write_bytes(b"x")
    return True


def recording_download(sink):
    """fake_download that appends (url, fallback) to sink."""
    def download(session, url, dest, fallback=None):
        sink.append((url, fallback))
        dest.write_bytes(b"x")
        return True
    return download
