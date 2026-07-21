"""Turning URLs and API payloads into usernames, ids and file names."""

import os
import re
import sys
from urllib.parse import unquote, urlparse


def extract_username(profile_url: str) -> str:
    """Extract the username from a DeviantArt profile URL."""
    parsed = urlparse(profile_url if "://" in profile_url else f"https://{profile_url}")
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
    if re.match(r"^[A-Za-z0-9.-]+$", profile_url) and "." not in profile_url:
        return profile_url

    sys.exit(f"Could not extract a username from: {profile_url}")


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


def guess_extension(url: str) -> str:
    path = unquote(urlparse(url).path)
    ext = os.path.splitext(path)[1].lower()
    return ext if ext and len(ext) <= 5 else ".jpg"
