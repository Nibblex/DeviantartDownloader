"""Orchestration: list a gallery, route each work, download the lot."""

import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, NamedTuple

from .api import DeviantArtClient
from .constants import API_SUBDIR, CANCEL, WEB_SUBDIR, CancelledByUser, say
from .controls import KeyboardControls, set_progress
from .downloads import process_deviation
from .listing import list_gallery, resolve_via_api
from .literature import is_text_work
from .manifest import DownloadManifest
from .naming import deviation_key, deviation_title, profile_label
from .storage import read_json, write_json
from .web import WebClient, needs_api

STATUSES = ("downloaded", "replaced", "skipped", "failed", "no_media", "cancelled")

# The friends endpoint caps its page size lower than the gallery listing does.
WATCH_PAGE_LIMIT = 50


def _quit_before_download() -> None:
    """Exit after 'q' was pressed during listing/routing, before any download."""
    print("\nStopped before downloading (nothing to clean up). "
          "Run the same command again to resume.")
    sys.exit(130)


class Axis(NamedTuple):
    """One axis of --only: its selectors, and how a work answers them.

    `reads` answers the question the first value asks -- is this an image, is it
    mature -- so naming a value keeps the works that answer it that way, and
    naming every value of an axis is the same as naming none: the axis then
    filters nothing. An axis with a single value is the degenerate case of the
    same rule, wanted or not mentioned.

    `unreported` is what the API listing does not say about a work, for the
    axes only the website reports; empty when both routes know the answer.
    """
    values: tuple[str, ...]
    reads: Callable[[dict], bool]
    unreported: str = ""


AXES = (
    Axis(("images", "literature"), lambda dev: not is_text_work(dev)),
    Axis(("mature",), lambda dev: bool(dev.get("is_mature"))),
    # These two ride on the website listing alone, and the API has no
    # equivalent, so their flag is None -- not known -- on that route.
    Axis(("ai", "no-ai"), lambda dev: dev.get("is_ai_generated") is True,
         "whether a work is AI-generated"),
    Axis(("upscaled", "no-upscaled"), lambda dev: dev.get("is_upscaled") is True,
         "whether a work was upscaled with AI"),
)

ONLY_FILTERS = tuple(value for axis in AXES for value in axis.values)


def only_tokens(values) -> frozenset[str]:
    """The selector words in a --only value, however it was written.

    Repeated words, comma-separated ones, or both; unknown words are left in,
    for the caller to reject in the terms of wherever the value came from.
    """
    return frozenset(
        token for value in values or ()
        for token in str(value).lower().replace(",", " ").split() if token)


def parse_only(values) -> frozenset[str]:
    """The --only selectors, given as repeated words, commas, or both."""
    chosen = only_tokens(values)
    unknown = sorted(chosen - frozenset(ONLY_FILTERS))
    if unknown:
        # --only reads every word after it, so a profile written behind it is
        # swallowed as a selector; say so rather than leave it to be guessed.
        sys.exit(f"--only takes {', '.join(ONLY_FILTERS)}, not: {', '.join(unknown)}\n"
                 "If that is the profile, put it before --only, which reads "
                 "every word that follows it.")
    return chosen


def _selectors(only) -> frozenset[str]:
    """The --only selection as a set, for whatever a caller passed.

    A bare string would iterate as its characters and quietly filter nothing.
    """
    if not only:
        return frozenset()
    return frozenset([only]) if isinstance(only, str) else frozenset(only)


def _chosen(only: frozenset[str], axis: Axis) -> str | None:
    """The single value of an axis that `only` narrows to.

    None when the selection names every value of the axis or none of them,
    which is the same thing: an axis whose values are all wanted filters
    nothing.
    """
    chosen = only & frozenset(axis.values)
    return next(iter(chosen)) if len(chosen) == 1 else None


def _wants(axis: Axis, chosen: str) -> bool:
    """What a work must read as, for this selector to keep it."""
    return chosen == axis.values[0]


def filter_by_content(deviations: list[dict],
                      only: frozenset[str] | None) -> tuple[list[dict], int]:
    """Keep the works matching everything `only` asks for. Returns (kept, dropped).

    The selectors sit on the axes of AXES, so they combine the way filters
    usually do: a union within an axis, an intersection across them. "images"
    and "literature" are the two values of what kind of work it is, so naming
    both is the same as naming neither, while "mature" is a separate axis and
    narrows whatever the kind left. That is what makes `--only literature mature`
    mean the mature literature rather than everything that is either.

    On the two axes only the website reports, a work whose flag never arrived
    reads as False, which makes the selectors deliberately lopsided: "ai" asks
    for the works known to be AI-made, while "no-ai" keeps what is not known to
    be, rather than discarding a work over a fact the listing never carried.
    """
    only = _selectors(only)
    if not only:
        return deviations, 0
    kept = deviations
    for axis in AXES:
        if chosen := _chosen(only, axis):
            wanted = _wants(axis, chosen)
            kept = [d for d in kept if axis.reads(d) == wanted]
    return kept, len(deviations) - len(kept)


def unreported_warnings(only: frozenset[str] | None,
                        from_web: bool) -> list[str]:
    """Say which selectors have no data, when the API did the listing.

    Some axes ride on the website listing alone, so selecting on one against an
    API listing silently means "nothing" or "everything" rather than what was
    asked for; which of the two it is falls out of what the selector does with
    a work the listing said nothing about. One line per axis, and none at all
    when there is nothing to warn about.
    """
    if from_web:
        return []
    only = _selectors(only)
    warnings = []
    for axis in AXES:
        chosen = _chosen(only, axis) if axis.unreported else None
        if chosen is None:
            continue
        # {} is a work nothing is known about, which is every work on this route.
        keeps_unknown = axis.reads({}) == _wants(axis, chosen)
        outcome = "drops no work" if keeps_unknown else "matches no work"
        warnings.append(
            f"  WARNING: the API listing does not report {axis.unreported}, so "
            f"--only {chosen} has nothing to select on and {outcome} here.")
    return warnings


def human_size(nbytes: float) -> str:
    """Format a byte count as a human-readable string (e.g. "45.3 MB")."""
    size = float(nbytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def new_stats() -> dict:
    """A fresh, zeroed statistics accumulator for one gallery or a whole run."""
    stats = {status: 0 for status in STATUSES}
    stats["bytes"] = 0
    stats["elapsed"] = 0.0
    # Keyed by the subdir each route writes to, so a job's subdir indexes its
    # own counters with no translation in between.
    stats[WEB_SUBDIR] = {"downloaded": 0, "bytes": 0}
    stats[API_SUBDIR] = {"downloaded": 0, "bytes": 0}
    return stats


def add_stats(dest: dict, src: dict) -> None:
    """Fold one gallery's stats into a running total (in place)."""
    for status in STATUSES:
        dest[status] += src[status]
    dest["bytes"] += src["bytes"]
    dest["elapsed"] += src["elapsed"]
    for route in (WEB_SUBDIR, API_SUBDIR):
        dest[route]["downloaded"] += src[route]["downloaded"]
        dest[route]["bytes"] += src[route]["bytes"]


def summary_lines(stats: dict, *, users: int | None = None) -> list[str]:
    """Build the detailed, multi-line summary shown at the end of a run.

    The first line keeps the compact "Downloaded: N | Skipped ..." shape; the
    indented lines below break the downloads down by route, with item counts
    and total size, and add a few derived metrics (speed, average file size).
    """
    lines = [
        f"Downloaded: {stats['downloaded']} "
        f"| Skipped (already existed): {stats['skipped']} "
        f"| No file: {stats['no_media']} | Failed: {stats['failed']}"
    ]
    if stats["replaced"]:
        lines[0] += f" | Replaced (were blurred): {stats['replaced']}"
    if stats["cancelled"]:
        lines[0] += f" | Cancelled: {stats['cancelled']}"

    # A repair pass can replace plenty while downloading nothing new, so the
    # breakdown counts both kinds of write.
    written = stats["downloaded"] + stats["replaced"]
    if written:
        web, api = stats[WEB_SUBDIR], stats[API_SUBDIR]
        lines.append(
            f"  · via website: {web['downloaded']} item(s), "
            f"{human_size(web['bytes'])}"
        )
        lines.append(
            f"  · via API:     {api['downloaded']} item(s), "
            f"{human_size(api['bytes'])}"
        )
        total = f"  · Total downloaded: {human_size(stats['bytes'])}"
        elapsed = stats["elapsed"]
        if elapsed >= 0.05:
            total += f" in {elapsed:.1f}s ({human_size(stats['bytes'] / elapsed)}/s)"
        total += f", avg {human_size(stats['bytes'] / written)}/file"
        if users:
            total += f", across {users} user(s)"
        lines.append(total)
    return lines


def discover_users(output_root: Path) -> list[str]:
    """List the users already downloaded to the output folder.

    A user is any subdirectory created by a previous run, recognised by the
    marker files the tool writes (_downloaded.json / _metadata.json), so
    unrelated folders the user may keep in the output directory are ignored.
    """
    if not output_root.is_dir():
        sys.exit(
            f"No profile given and the output folder does not exist: {output_root}\n"
            "Pass a profile (URL or username) to download a gallery first."
        )
    users = sorted(
        d.name for d in output_root.iterdir()
        if d.is_dir()
        and not d.name.startswith((".", "_"))
        and any((d / marker).is_file()
                for marker in ("_downloaded.json", "_metadata.json"))
    )
    if not users:
        sys.exit(
            f"No previously downloaded users found in: {output_root}\n"
            "Pass a profile (URL or username) to download a gallery first."
        )
    return users


def fetch_watching(client: DeviantArtClient) -> list[str]:
    """List the users the logged-in account watches.

    The friends endpoint reads the watchlist of the account behind the token
    when no username is given, which is why this needs the session --login
    saves: an application token belongs to no deviant and watches nobody.
    """
    if not client.user_mode:
        sys.exit(
            "--watching needs your DeviantArt account: run --login first.\n"
            "Without a saved session the token belongs to the application, "
            "which watches nobody."
        )
    usernames, offset = [], 0
    while True:
        data = client.api_get("user/friends", params={
            "offset": offset, "limit": WATCH_PAGE_LIMIT, "mature_content": "true",
        })
        results = data.get("results", [])
        usernames += [name for r in results
                      if (name := (r.get("user") or {}).get("username"))]
        say(f"  Page at offset {offset}: {len(results)} watched user(s) "
            f"(total: {len(usernames)})")
        if not data.get("has_more"):
            break
        offset = data.get("next_offset") or offset + WATCH_PAGE_LIMIT
    if not usernames:
        sys.exit("Your account is not watching anybody, so there is nothing to sync.")
    return usernames


def worth_repairing(output_root: Path, username: str) -> bool:
    """True when --redownload-blurred could improve anything this user holds.

    It only ever replaces files already on disk, and only the API route ever
    handed back a blur, so a gallery recorded entirely under web/ cannot be
    improved and the whole listing walk the flag forces would find nothing.
    Answered off the download record, before a single request.
    """
    out_dir = output_root / username
    return out_dir.is_dir() and DownloadManifest(out_dir).has_api_route_files()


def sync_gallery(
    client: DeviantArtClient, username: str, output_root: Path, *,
    web_workers: int, api_workers: int,
    redownload_missing: bool, unblur: bool, redownload_blurred: bool = False,
    full: bool = False, web: WebClient | None = None, gallery: str | None = None,
    text_format: str = "txt", only: frozenset[str] | None = None,
    overrides: "UserOverrides | None" = None,
) -> dict | None:
    """Download every new work of one user. Returns the counts per status,
    or None when the gallery is empty.

    With a gallery name only that folder is downloaded instead of the whole
    gallery. Exits with code 130 if the user interrupts with Ctrl+C.

    `only` and `text_format` are what the command line asked of every user;
    `overrides` is the per-user settings file, which may replace either of them
    for this one. It is consulted after the listing because that is what carries
    the id it recognises a renamed user by.

    A profile that has gone since it was listed raises UserNotFoundError, from
    whichever call happens to discover it -- the gallery listing, the mature
    lookup, a folder resolution. Deciding what that means is the caller's, who
    is the one that knows whether the profile was asked for by name.
    """
    out_dir = output_root / username
    # Loading the manifest before fetching lets the listing stop at the
    # first fully downloaded page. --redownload-missing needs the whole
    # listing: the files it restores are recorded in the manifest, so the
    # early stop would hide them.
    manifest = DownloadManifest(out_dir) if out_dir.is_dir() else None

    print(f"User: {profile_label(username)}")
    if gallery:
        print(f'Gallery folder: "{gallery}"')
    say("Fetching gallery listing...")
    meta_path = out_dir / "_metadata.json"
    previous_meta = read_json(meta_path, [])
    # Manifests written by API-only versions are keyed by UUID, which the
    # website route cannot match; the saved metadata bridges both ids.
    if manifest is not None and web is not None and previous_meta:
        migrated = manifest.adopt_web_keys(previous_meta)
        if migrated:
            say(f"  Re-keyed {migrated} previously downloaded work(s) so both "
                "routes recognise them.")

    # Both revisit works already recorded, which the incremental early stop is
    # built to hide, so either one has to walk the listing whole.
    revisiting = redownload_missing or redownload_blurred
    listing_full = full or revisiting
    # The controls cover the whole job, so 'q' stops a long listing too and 'p'
    # pauses it; the listing and routing loops watch CANCEL/RESUME themselves.
    with KeyboardControls():
        set_progress(f"listing {username}")
        try:
            deviations, from_web = list_gallery(client, web, username,
                                                manifest=manifest, full=listing_full,
                                                gallery=gallery)
        except CancelledByUser:               # 'q' during a rate-limit wait
            _quit_before_download()
        if CANCEL.is_set():                   # 'q' between listing pages
            _quit_before_download()
        if not deviations:
            return None
        if overrides is not None:
            only, text_format = overrides.for_user(username, deviations,
                                                   only, text_format)
        for warning in unreported_warnings(only, from_web):
            print(warning)
        deviations, dropped = filter_by_content(deviations, only)
        if dropped:
            say(f"  Content filter (--only {' '.join(sorted(only))}): skipped "
                f"{dropped} of {dropped + len(deviations)} work(s).")
        if not deviations:
            print(f"No {' '.join(sorted(only))} to download in this gallery.")
            return new_stats()
        say(f"\nTotal works found: {len(deviations)}\n")

        out_dir.mkdir(parents=True, exist_ok=True)
        if manifest is None:
            manifest = DownloadManifest(out_dir)

        # Route each work: whatever the website serves in full goes through the
        # website, the rest (mature content) through the API.
        web_devs = [d for d in deviations if not needs_api(d)]
        blocked = [d for d in deviations if needs_api(d)]
        if from_web and blocked:
            try:
                blocked = resolve_via_api(client, username, blocked, deviations,
                                          manifest=manifest,
                                          redownload=revisiting,
                                          gallery=gallery)
            except CancelledByUser:           # 'q' during a rate-limit wait
                _quit_before_download()
        if CANCEL.is_set():                   # 'q' during the mature-work lookup
            _quit_before_download()
        jobs = [(d, WEB_SUBDIR) for d in web_devs] + [(d, API_SUBDIR) for d in blocked]
        if from_web:
            say(f"Route: {len(web_devs)} via the website ({WEB_SUBDIR}/), "
                f"{len(blocked)} via the API ({API_SUBDIR}/).\n")

        # Save the full metadata in case it is needed later. Merge with the
        # previous file so works beyond the early stop point are kept.
        metadata = deviations
        if previous_meta:
            fetched = {deviation_key(d) for d in deviations}
            metadata = deviations + [
                d for d in previous_meta
                if isinstance(d, dict) and deviation_key(d) not in fetched
            ]
        write_json(meta_path, metadata)

        counts = new_stats()
        total = len(jobs)
        done = 0
        interrupted = False
        started = time.monotonic()

        # Each route gets its own pool, so the website threads stay exclusive to
        # the website and the API runs at a lower, separate concurrency cap (the
        # DA_API_WORKERS "semaphore") that keeps parallel API requests from
        # tripping the rate limit.
        with ThreadPoolExecutor(max_workers=web_workers) as web_pool, \
             ThreadPoolExecutor(max_workers=api_workers) as api_pool:
            futures = {}
            for dev, subdir in jobs:
                pool = api_pool if subdir == API_SUBDIR else web_pool
                futures[pool.submit(
                    process_deviation, client, dev, out_dir, manifest,
                    redownload_missing, unblur,
                    redownload_blurred=redownload_blurred,
                    dest_dir=out_dir / subdir,
                    session=web.session if subdir == WEB_SUBDIR else None,
                    use_api=subdir == API_SUBDIR, web=web,
                    text_format=text_format)] = (dev, subdir)
            try:
                for future in as_completed(futures):
                    done += 1
                    dev, subdir = futures[future]
                    try:
                        status, message = future.result()
                    except Exception as e:
                        status, message = "failed", f"Unexpected ERROR: {e}"
                    counts[status] += 1
                    if status in ("downloaded", "replaced"):
                        # The file's size comes off disk: the manifest records
                        # the path this route wrote it to, keyed by the work's id.
                        rel = manifest.filename_for(deviation_key(dev))
                        dest = out_dir / rel if rel else None
                        size = dest.stat().st_size if dest and dest.is_file() else 0
                        counts[subdir]["downloaded"] += 1
                        counts[subdir]["bytes"] += size
                        counts["bytes"] += size
                    # A failure, an empty work or a cancellation is a result,
                    # not progress: -q drops the running commentary, never the
                    # works that did not make it.
                    line = say if status in ("downloaded", "skipped") else print
                    line(f"[{done}/{total}] {message}")
                    # The footer names the route too: the scrolling lines carry
                    # it in the path (web/… or api/…), but under -q the footer is
                    # all there is, and the two behave nothing alike -- the API
                    # one is metered and paced, the website one free.
                    set_progress(f"{done}/{total}  {subdir}  {deviation_title(dev)}")
                    if CANCEL.is_set():           # the user pressed 'q'
                        interrupted = True
                        web_pool.shutdown(cancel_futures=True)
                        api_pool.shutdown(cancel_futures=True)
                        break
            except KeyboardInterrupt:
                interrupted = True
                CANCEL.set()
                print("\nCtrl+C received: stopping downloads and cleaning up "
                      "partial files...")
                web_pool.shutdown(cancel_futures=True)
                api_pool.shutdown(cancel_futures=True)

    counts["elapsed"] = time.monotonic() - started
    lines = summary_lines(counts)
    if interrupted:
        print(f"\nInterrupted ({done} of {total} works processed). {lines[0]}")
        for line in lines[1:]:
            print(line)
        print(f"Files saved to: {out_dir.resolve()}")
        print("Run the same command again to resume where it left off.")
        sys.exit(130)
    print(f"\nDone. {lines[0]}")
    for line in lines[1:]:
        print(line)
    print(f"Files saved to: {out_dir.resolve()}")
    return counts
