"""The website's own JSON endpoints, which cost no API quota.

These are undocumented and may change without notice, so every failure is
raised as WebError and the caller falls back to the API.
"""

import re
import threading

import requests

from .constants import (BROWSER_USER_AGENT, CANCEL, GALLECTION_URL, WEB_BASE,
                        WEB_SUBDIR)


class WebError(RuntimeError):
    """The website route is unusable; the caller falls back to the API."""


class WebClient:
    """Reader for the JSON endpoints the DeviantArt website calls itself.

    No OAuth is involved, so nothing here counts against the API quota. The
    endpoints are undocumented and may change without notice: every failure
    is raised as WebError so the caller can fall back to the API.
    """

    def __init__(self):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = BROWSER_USER_AGENT
        self._csrf: str | None = None
        self._csrf_lock = threading.Lock()

    def _ensure_csrf(self, username: str) -> str:
        """Pick up the CSRF token the website embeds in every page."""
        with self._csrf_lock:
            if self._csrf:
                return self._csrf
            url = f"{WEB_BASE}/{username}/gallery/all"
            try:
                resp = self.session.get(url, timeout=30)
            except requests.RequestException as e:
                raise WebError(f"could not open {url}: {e}") from e
            if resp.status_code != 200:
                raise WebError(f"{url} answered HTTP {resp.status_code}")
            m = re.search(r"""window\.__CSRF_TOKEN__\s*=\s*['"]([^'"]+)""", resp.text)
            if not m:
                raise WebError(f"no CSRF token found in {url}")
            self._csrf = m.group(1)
            return self._csrf

    def gallery_page(self, username: str, offset: int, limit: int) -> dict:
        """One page of the gallery listing, in the website's own format."""
        params = {
            "username": username,
            "type": "gallery",
            "all_folder": "true",
            "offset": offset,
            "limit": limit,
            "da_minor_version": "20230710",
        }
        for attempt in range(3):
            params["csrf_token"] = self._ensure_csrf(username)
            try:
                resp = self.session.get(GALLECTION_URL, params=params, timeout=30)
            except requests.RequestException as e:
                raise WebError(f"gallery listing request failed: {e}") from e
            if resp.status_code == 400:
                # Expired or rejected token: drop it and ask for a fresh one.
                with self._csrf_lock:
                    self._csrf = None
                continue
            if resp.status_code == 429:
                wait = 5 * (attempt + 1)
                print(f"  Website throttling the listing, waiting {wait} s...")
                if CANCEL.wait(wait):
                    raise RuntimeError("Cancelled by the user")
                continue
            if resp.status_code != 200:
                raise WebError(f"gallery listing answered HTTP {resp.status_code}")
            try:
                return resp.json()
            except ValueError as e:
                raise WebError(f"gallery listing returned no JSON: {e}") from e
        raise WebError("the gallery listing kept rejecting our requests")


def web_media_url(media: dict) -> str | None:
    """Build the full-resolution URL of a work out of its listing entry.

    The listing already carries everything needed: a base URI, a signed token
    and one entry per available size. The `fullview` entry is the largest one;
    when it names a transformation (`c`) the token only authorizes that exact
    path, otherwise the untransformed base URI is the original file.
    """
    base = media.get("baseUri")
    if not base:
        return None
    types = media.get("types") or []
    full = next((t for t in types if t.get("t") == "fullview"), None)
    if full is None:
        return None
    tokens = media.get("token") or []
    url = base
    if full.get("c"):
        url += full["c"].replace("<prettyName>", media.get("prettyName") or "")
    if tokens:
        # Each size names the token that signs it; index 0 is the safe default.
        index = full.get("r", 0)
        url += "?token=" + tokens[index if 0 <= index < len(tokens) else 0]
    return url


def normalize_web_deviation(item: dict) -> dict:
    """Translate a website listing entry into the shape the API returns.

    Only the fields the downloader actually reads are mapped, plus the block
    information that decides which route a work takes.
    """
    media = item.get("media") or {}
    src = web_media_url(media) if item.get("type") == "image" else None
    return {
        "deviationid": str(item.get("deviationId") or ""),
        "title": item.get("title") or "untitled",
        "url": item.get("url") or "",
        "published_time": item.get("publishedTime"),
        "is_mature": bool(item.get("isMature")),
        "is_downloadable": bool(item.get("isDownloadable")),
        "is_blocked": bool(item.get("isBlocked")),
        "block_reasons": list(item.get("blockReasons") or []),
        "content": {"src": src} if src else None,
        "_source": WEB_SUBDIR,
    }


def needs_api(dev: dict) -> bool:
    """True when the website only serves this work blurred or not at all.

    Mature works are listed to logged-out visitors with a placeholder image
    whose token is pinned to the blurred transformation, so the real file can
    only come from the API (unblurred if --login was used). Works blocked for
    any other reason get the same placeholder, so they take the same route.
    """
    if dev.get("_source") != WEB_SUBDIR:
        return True
    return bool(dev.get("is_blocked")) or bool(
        dev.get("is_mature") and not dev.get("content"))
