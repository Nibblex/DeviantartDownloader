"""Inspecting a profile: its facts, stats and galleries, without downloading.

The website 'about' module carries the rich header (birthday, join age, links,
badges) and the full user stats at no API quota; the API user profile fills in
what the website omits (bio, real name, human-readable specialty). Either source
alone still yields a useful summary, so a failure of one degrades gracefully.
"""

import re
from datetime import date

from .api import DeviantArtClient
from .constants import WEB_BASE
from .listing import fetch_api_folders
from .web import WebClient, WebError

# 365.25-day years, matching how DeviantArt counts "Deviant for X years".
_SECONDS_PER_YEAR = 31_557_600


def gather_profile(client: DeviantArtClient, web: WebClient | None,
                   username: str) -> dict:
    """Collect a profile's facts, stats and gallery folders into one dict."""
    info = {"username": username, "profile_url": f"{WEB_BASE}/{username}",
            "galleries": None}
    if web is not None:
        try:
            info.update(_from_web_about(web.profile_about(username)))
            info["galleries"] = _folders(web.list_folders(username))
        except WebError as e:
            print(f"  Website profile unavailable ({e}); falling back to the API.")
    # The API fills what the website leaves out (bio, real name, specialty),
    # and everything when the website route was unavailable.
    api = client.api_get(f"user/profile/{username}",
                         params={"mature_content": "true"})
    _fill_missing(info, _from_api_profile(api))
    if info["galleries"] is None:
        info["galleries"] = _folders(
            fetch_api_folders(client, username, calculate_size=True))
    return info


def _fill_missing(info: dict, extra: dict):
    """Add extra fields only where info has nothing meaningful yet."""
    for key, value in extra.items():
        if value in (None, "", []) or info.get(key):
            continue
        info[key] = value


# ---------------------------------------------------------------------------
# Source-specific extraction
# ---------------------------------------------------------------------------

def _module(about: dict, name: str) -> dict:
    """The moduleData payload of a named module in an 'about' response."""
    page = (about.get("gruser") or {}).get("page") or {}
    for module in page.get("modules") or []:
        if module.get("name") == name:
            return (module.get("moduleData") or {}).get(name) or {}
    return {}


def _from_web_about(about: dict) -> dict:
    a = _module(about, "about")
    stats = _module(about, "userstats")
    out = {
        "country": a.get("country"),
        "website": a.get("website"),
        "website_label": a.get("websiteLabel"),
        "twitter": a.get("twitterUsername"),
        "gender": a.get("gender"),
        "tagline": (a.get("tagline") or "").strip(),
        "is_artist": a.get("isArtist"),
        "birthday": _birthday(a),
        "age": a.get("age"),
        "deviant_for_years": _years(a.get("deviantFor")),
        "badges": [b.get("title") for b in (a.get("badges") or []) if b.get("title")],
    }
    if stats:
        out["stats"] = {
            "deviations": stats.get("deviations"),
            "watchers": stats.get("watchers"),
            "watching": stats.get("watching"),
            "pageviews": stats.get("pageviews"),
            "favourites": stats.get("favourites"),
            "comments_received": stats.get("commentsReceivedProfile"),
            "comments_made": stats.get("commentsMade"),
        }
    return out


def _from_api_profile(api: dict) -> dict:
    st = api.get("stats") or {}
    return {
        "real_name": (api.get("real_name") or "").strip(),
        "bio": _plain_text(api.get("bio")),
        "tagline": (api.get("tagline") or "").strip(),
        "country": api.get("country"),
        "website": api.get("website"),
        "specialty": api.get("artist_specialty"),
        "profile_url": api.get("profile_url"),
        "is_artist": api.get("user_is_artist"),
        "stats": {
            "deviations": st.get("user_deviations"),
            "favourites": st.get("user_favourites"),
            "comments_made": st.get("user_comments"),
            "pageviews": st.get("profile_pageviews"),
            "comments_received": st.get("profile_comments"),
        },
    }


def _folders(folders: list[dict]) -> list[dict]:
    """Name + item count per gallery folder (size is None if unknown)."""
    return [{"name": f.get("name") or "Untitled", "size": f.get("size")}
            for f in folders]


def _birthday(about: dict) -> str | None:
    year, month, day = about.get("dobYear"), about.get("dobMonth"), about.get("dobDay")
    if not (month and day):
        return None
    try:
        label = date(2000, month, day).strftime("%-d %B")
    except ValueError:
        return None
    return f"{label} {year}" if year else label


def _years(seconds) -> int | None:
    return int(seconds // _SECONDS_PER_YEAR) if seconds else None


def _plain_text(html) -> str | None:
    if not html:
        return None
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"[ \t]+\n", "\n", text).strip() or None


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _num(value) -> str:
    return f"{value:,}" if isinstance(value, int) else "?"


def format_profile(info: dict) -> str:
    lines = [f"Profile: {info['username']}"
             + (f" ({info['real_name']})" if info.get("real_name") else "")]
    lines.append(f"  {info['profile_url']}")

    if info.get("tagline"):
        lines.append(f"  Tagline: {info['tagline']}")
    if info.get("is_artist"):
        specialty = f" — {info['specialty']}" if info.get("specialty") else ""
        lines.append(f"  Artist{specialty}")
    if info.get("country"):
        lines.append(f"  Location: {info['country']}")
    if info.get("birthday"):
        age = f" (age {info['age']})" if info.get("age") else ""
        lines.append(f"  Birthday: {info['birthday']}{age}")
    if info.get("deviant_for_years") is not None:
        lines.append(f"  Deviant for: {info['deviant_for_years']} years")
    if info.get("gender"):
        lines.append(f"  Gender: {info['gender']}")

    links = []
    if info.get("website"):
        label = f" ({info['website_label']})" if info.get("website_label") else ""
        links.append(f"{info['website']}{label}")
    if info.get("twitter"):
        links.append(f"twitter: @{info['twitter']}")
    if links:
        lines.append(f"  Links: {', '.join(links)}")

    if info.get("bio"):
        bio = "\n         ".join(info["bio"].splitlines())
        lines.append(f"  Bio:     {bio}")

    stats = info.get("stats") or {}
    if any(v is not None for v in stats.values()):
        lines.append("Statistics:")
        row1 = [("Deviations", stats.get("deviations")),
                ("Watchers", stats.get("watchers")),
                ("Watching", stats.get("watching"))]
        row2 = [("Pageviews", stats.get("pageviews")),
                ("Favourites", stats.get("favourites"))]
        row3 = [("Comments received", stats.get("comments_received")),
                ("Comments made", stats.get("comments_made"))]
        for row in (row1, row2, row3):
            shown = [f"{label}: {_num(v)}" for label, v in row if v is not None]
            if shown:
                lines.append("  " + " | ".join(shown))

    badges = info.get("badges") or []
    if badges:
        head = ", ".join(badges[:8])
        more = f" (+{len(badges) - 8} more)" if len(badges) > 8 else ""
        lines.append(f"Badges: {head}{more}")

    galleries = info.get("galleries") or []
    total = sum(g["size"] for g in galleries if isinstance(g.get("size"), int))
    header = f"Galleries: {len(galleries)} folder(s)"
    if total:
        header += f", {total:,} items"
    lines.append(header)
    for g in galleries:
        count = f" — {_num(g['size'])} items" if g.get("size") is not None else ""
        lines.append(f"  - {g['name']}{count}")

    return "\n".join(lines)


def print_profile(client: DeviantArtClient, web: WebClient | None, username: str):
    """Fetch and print a profile summary; downloads nothing."""
    print(f"User: {username}")
    print("Fetching profile info...")
    print()
    print(format_profile(gather_profile(client, web, username)))
