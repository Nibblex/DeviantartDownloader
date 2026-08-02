"""Inspecting a profile: its facts, stats and galleries, without downloading.

The website 'about' module carries the rich header (birthday, join age, links,
badges), the bio and the full user stats at no API quota, so it answers a whole
profile on its own; the API user profile is asked only when the website route is
unavailable, and adds the two fields the website does not publish (the real name
and a human-readable specialty). Either source alone still yields a useful
summary, so a failure of one degrades gracefully.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime

from .api import UNREADABLE_PROFILE, DeviantArtClient
from .constants import say
from .listing import fetch_api_folders, list_gallery
from .literature import KIND_HTML, classify_web_html, render
from .naming import profile_label
from .sync import date_range_label, filter_by_date
from .web import WebClient, WebError, web_media_url

# 365.25-day years, matching how DeviantArt counts "Deviant for X years".
_SECONDS_PER_YEAR = 31_557_600


def gather_profile(client: DeviantArtClient, web: WebClient | None,
                   username: str, *, since: datetime | None = None,
                   until: datetime | None = None) -> dict:
    """Collect a profile's facts, stats and gallery folders into one dict.

    The API profile costs one request per user, which --watching --info over a
    long watchlist multiplies into the rate limit, so it is only asked when the
    website could not answer: on that route the summary loses the real name and
    the readable specialty, which the website does not publish. --force-api
    leaves `web` None and so brings those two back -- at the cost of everything
    only the website has (badges, birthday, join age, links, watcher counts, and
    the larger avatar and banner), because it is a swap of source, not a union.

    The bio is not among the losses, despite the API having a field for it: on
    current profiles that field comes back empty while the website carries the
    whole text, because the bio moved to the editor the website renders. Adding
    the API call back would not recover it.
    """
    info = {"username": username, "galleries": None}
    if web is not None:
        try:
            info.update(_from_web_about(web.profile_about(username)))
            info["galleries"] = _folders(web.list_folders(username))
        except WebError as e:
            print(f"  Website profile unavailable ({e}); falling back to the API.")
    # No folders means the website never answered, which is the one case the
    # API is worth a request for.
    if info["galleries"] is None:
        api = client.api_get(f"user/profile/{username}",
                             params={"mature_content": "true"})
        _fill_missing(info, _from_api_profile(api))
        info["galleries"] = _folders(
            fetch_api_folders(client, username, calculate_size=True))
    if since is not None or until is not None:
        info["range_label"] = date_range_label(since, until)
        info["in_range"] = _count_in_range(client, web, username, since, until)
    return info


def _count_in_range(client: DeviantArtClient, web: WebClient | None,
                    username: str, since: datetime | None,
                    until: datetime | None) -> int:
    """How many of a user's works fall within the date bounds.

    The one part of a summary that has to walk the gallery listing: the folder
    counts both routes publish are totals, with no breakdown by date, so there
    is nothing cheaper to read the answer off. --since bounds the walk -- a page
    wholly older than it ends the listing -- so asking about a recent window
    costs a page or two, while asking about the whole gallery costs the gallery.

    Which is why this is only done when a bound was actually given: a plain
    --info stays the two round trips per user it has always been.
    """
    deviations, _ = list_gallery(client, web, username, manifest=None,
                                 full=False, since=since)
    kept, _ = filter_by_date(deviations, since, until)
    return len(kept)


def _fill_missing(info: dict, extra: dict):
    """Add extra fields only where info has nothing meaningful yet."""
    for key, value in extra.items():
        if value in (None, "", []) or info.get(key):
            continue
        info[key] = value


# ---------------------------------------------------------------------------
# Source-specific extraction
# ---------------------------------------------------------------------------

def _module(about: dict, name: str, key: str | None = None) -> dict:
    """The moduleData payload of a named module in an 'about' response.

    Most modules key their payload by their own name; the ones that do not (the
    cover answers under "coverDeviation") name that key with `key`.
    """
    page = (about.get("gruser") or {}).get("page") or {}
    for module in page.get("modules") or []:
        if module.get("name") == name:
            return (module.get("moduleData") or {}).get(key or name) or {}
    return {}


def _from_web_about(about: dict) -> dict:
    a = _module(about, "about")
    stats = _module(about, "userstats")
    owner = about.get("owner") or {}
    # The banner is a deviation like any other, so its listing entry carries the
    # media block the full-resolution URL is built from.
    cover = _module(about, "cover_deviation", "coverDeviation")
    out = {
        # The website serves the large avatar and the banner at full resolution,
        # both of which the API only offers in smaller sizes.
        "avatar": owner.get("usericon"),
        "banner": web_media_url((cover.get("coverDeviation") or {}).get("media") or {}),
        "bio": _web_bio(a.get("textContent")),
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


def _web_bio(text_content: object) -> str | None:
    """The bio the website carries, in whichever editor's format it was written.

    It is the same shape as a literature work's body, so the renderer that
    module already has turns either format into plain text.
    """
    classified = classify_web_html((text_content or {}).get("html"))
    if classified is None:
        return None
    return render(*classified, "txt") or None


def _from_api_profile(api: dict) -> dict:
    st = api.get("stats") or {}
    # The banner is a deviation of its own; `cover_photo` is the older field and
    # comes back empty for most profiles, so it only serves as a last resort.
    cover = (api.get("cover_deviation") or {}).get("cover_deviation") or {}
    banner = (cover.get("content") or cover.get("preview") or {}).get("src")
    return {
        "avatar": (api.get("user") or {}).get("usericon"),
        "banner": banner or api.get("cover_photo") or None,
        "real_name": (api.get("real_name") or "").strip(),
        "bio": _api_bio(api.get("bio")),
        "tagline": (api.get("tagline") or "").strip(),
        "country": api.get("country"),
        "website": api.get("website"),
        "specialty": api.get("artist_specialty"),
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


def _api_bio(html) -> str | None:
    """The bio the API carries, which is an HTML fragment of the older editor.

    Rendered by the same code as the website's, so entities are unescaped and
    block breaks kept rather than run together.
    """
    return render(KIND_HTML, html or "", "txt") or None


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _num(value) -> str:
    return f"{value:,}" if isinstance(value, int) else "?"


def format_profile(info: dict) -> str:
    lines = [f"Profile: {profile_label(info['username'], info.get('real_name') or '')}"]

    if info.get("avatar"):
        lines.append(f"  Avatar: {info['avatar']}")
    if info.get("banner"):
        lines.append(f"  Banner: {info['banner']}")
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

    # Below the folder totals it qualifies, and only when a bound was given:
    # this is the one figure that cost a walk of the listing to answer.
    if info.get("in_range") is not None:
        lines.append(f"Published within {info['range_label']}: "
                     f"{_num(info['in_range'])} work(s)")

    return "\n".join(lines)


def print_profiles(client: DeviantArtClient, web: WebClient | None,
                   usernames: list[str], *, workers: int = 1,
                   skip_missing: bool = False, since: datetime | None = None,
                   until: datetime | None = None) -> None:
    """Print one summary per user; downloads nothing.

    The profiles are fetched concurrently and printed in the order asked for.
    A whole watchlist walked one at a time is two website round trips per user
    of pure latency, and that route costs no quota and is paced by nothing, so
    there is nothing to gain by going slowly. Each summary is printed as soon
    as its turn comes, while the ones behind it are still being fetched.

    With skip_missing a profile that cannot be read -- gone, deactivated, or
    blocked to us -- is reported and skipped, the way a batch download treats
    one; asking for a single profile by name still fails loudly, because a typo
    should not look empty.
    """
    say("Fetching profile info...\n")
    with ThreadPoolExecutor(max_workers=max(workers, 1)) as pool:
        pending = [pool.submit(gather_profile, client, web, name,
                               since=since, until=until)
                   for name in usernames]
        for index, (username, future) in enumerate(zip(usernames, pending)):
            if index:
                print()
            try:
                print(format_profile(future.result()))
            except UNREADABLE_PROFILE as e:
                if not skip_missing:
                    raise
                print(f"  {e}\nSkipping {username}.")
