"""Turning URLs and API payloads into usernames, ids and file names."""

import os
import re
import sys
from datetime import datetime, timezone
from urllib.parse import unquote, urlparse

from .constants import API_SUBDIR, WEB_BASE, WEB_SUBDIR


def profile_url(username: str) -> str:
    """The canonical profile URL of a username; the inverse of extract_username."""
    return f"{WEB_BASE}/{username}"


def profile_label(username: str, real_name: str = "") -> str:
    """How a user is named on screen: the username, real name and profile URL."""
    named = f"{username} ({real_name})" if real_name else username
    return f"{named} — {profile_url(username)}"


def extract_username(url: str) -> str:
    """Extract the username from a DeviantArt profile URL."""
    parsed = urlparse(url if "://" in url else f"https://{url}")
    host = parsed.netloc.lower()

    # Old format: https://username.deviantart.com
    m = re.match(r"^([a-z0-9-]+)\.deviantart\.com$", host)
    if m and m.group(1) != "www":
        return m.group(1)

    # Current format: https://www.deviantart.com/username[/...]
    if "deviantart.com" in host:
        parts = [p for p in parsed.path.split("/") if p]
        if parts:
            return parts[0]

    # If the username was passed directly
    if re.match(r"^[A-Za-z0-9.-]+$", url) and "." not in url:
        return url

    sys.exit(f"Could not extract a username from: {url}")


def username_from_url(url: str) -> str:
    """The username in a deviation/profile URL, or "" if there is none.

    Unlike extract_username this never exits: it is a best-effort helper for
    code paths where a missing username is recoverable.
    """
    parts = [p for p in urlparse(url or "").path.split("/") if p]
    return parts[0] if parts else ""


_URL_ID_RE = re.compile(r"-(\d+)$")


def deviation_key(dev: dict) -> str:
    """Identity of a work, comparable across both routes.

    The API identifies works by UUID and the website by a numeric id; the only
    thing both carry is the canonical URL, which ends in that numeric id. It is
    therefore the key of choice, with the UUID as a fallback for entries that
    have no URL.
    """
    m = _URL_ID_RE.search(dev.get("url") or "")
    if m:
        return m.group(1)
    return dev.get("deviationid") or ""


def user_ids(deviations: list[dict]) -> dict[str, str]:
    """The listed works' author id, under the name of the route that listed them.

    The same problem as deviation_key, one level up and without its answer: the
    two routes disagree about user ids -- the website reports a numeric one, the
    API a UUID, and for one and the same user those are different values -- and
    there is no third field both carry. Neither can stand in for the other, so
    each is kept under its own route's name. Whichever route ran, the id it
    reports is the part of a user that a rename does not change.
    """
    ids: dict[str, str] = {}
    for dev in deviations:
        route = WEB_SUBDIR if dev.get("_source") == WEB_SUBDIR else API_SUBDIR
        if route not in ids and (uid := (dev.get("author") or {}).get("userid")):
            ids[route] = str(uid)
    return ids


def deviation_title(dev: dict) -> str:
    """A work's title, with the placeholder used when it has none."""
    return dev.get("title") or "untitled"


def content_src(dev: dict) -> str:
    """The URL a listing entry offers for a work, or "" when it offers none."""
    return (dev.get("content") or {}).get("src") or ""


# The website writes an offset as "-0700" and sometimes ends a stamp with "Z".
# Python 3.10's fromisoformat, which this project still supports, accepts
# neither, so both are rewritten into the one spelling every version parses.
_ISO_OFFSET_RE = re.compile(r"([+-]\d{2})(\d{2})$")


def deviation_time(dev: dict) -> datetime | None:
    """When a work was published, in UTC, or None when the listing does not say.

    The two routes disagree about the format the way they disagree about ids:
    the API reports published_time as a Unix timestamp inside a string, the
    website as an ISO 8601 stamp carrying an offset. Both are read here so that
    everything above compares datetimes rather than formats.

    The answer is always aware and always UTC. A naive datetime cannot be
    compared with an aware one -- that raises rather than returning False --
    and a listing that mixed the two would fail on whichever work came first.
    """
    raw = str(dev.get("published_time") or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        try:
            return datetime.fromtimestamp(int(raw), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if raw.endswith(("Z", "z")):
        raw = raw[:-1] + "+00:00"
    try:
        stamp = datetime.fromisoformat(_ISO_OFFSET_RE.sub(r"\1:\2", raw))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def deviation_suffix(dev: dict) -> str:
    """Short, stable id to disambiguate file names sharing a title."""
    key = deviation_key(dev)
    return key if key.isdigit() else key[:8]


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name[:150] or "untitled"


def unblur_wixmp_url(url: str) -> str:
    """Remove the blur transform the API adds to mature-content previews.

    With client_credentials tokens the API serves mature deviations as a
    logged-out visitor would see them: content.src includes a ",blur_NN"
    parameter in the wixmp transformation segment. For older uploads the
    URL token authorizes any transformation, so stripping the blur yields
    the unblurred image. For newer uploads (~mid-2021 onwards) the token
    pins the exact path including the transformation segment, so the CDN
    answers 403; the caller must fall back to the original blurred URL.
    """
    if url.startswith("https://images-wixmp-"):
        return re.sub(r",blur_\d+", "", url, count=1)
    return url


# The CDN rejects a blur outside this range, whatever else is right about the URL.
MAX_WIXMP_BLUR = 100
_BLUR_RE = re.compile(r",blur_(\d+)")


def is_blurred(url: str) -> bool:
    """True when a URL asks the CDN for the blurred rendering of a work."""
    return url.startswith("https://images-wixmp-") and bool(_BLUR_RE.search(url))


def clamp_wixmp_blur(url: str) -> str:
    """Bring an out-of-range blur transform back to what the CDN accepts.

    DeviantArt serves some works through a blur beyond its own CDN's 0-100
    range (blur_171 seen in the wild), and those answer 400 "(blur) parameter
    has to be between 0 and 100" on both routes, so the work cannot be fetched
    at all. The signed token bounds the blur from below (blur >= 30) and never
    from above, so lowering it to the maximum keeps the URL authorized while
    making it valid, and the image comes back at full resolution.
    """
    if not url.startswith("https://images-wixmp-"):
        return url
    return _BLUR_RE.sub(
        lambda m: f",blur_{min(int(m.group(1)), MAX_WIXMP_BLUR)}", url)


def guess_extension(url: str) -> str:
    path = unquote(urlparse(url).path)
    ext = os.path.splitext(path)[1].lower()
    return ext if ext and len(ext) <= 5 else ".jpg"
