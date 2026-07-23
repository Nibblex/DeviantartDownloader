"""Orchestration: list a gallery, route each work, download the lot."""

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .api import DeviantArtClient, UserNotFoundError
from .constants import API_SUBDIR, CANCEL, WEB_SUBDIR
from .downloads import process_deviation
from .listing import list_gallery, resolve_via_api
from .manifest import DownloadManifest
from .naming import deviation_key
from .storage import read_json, write_json
from .web import WebClient, needs_api


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


def sync_gallery(
    client: DeviantArtClient, username: str, output_root: Path, *,
    delay: float, web_workers: int, api_workers: int,
    redownload_missing: bool, unblur: bool,
    full: bool = False, web: WebClient | None = None, gallery: str | None = None,
) -> dict | None:
    """Download every new work of one user. Returns the counts per status,
    or None when the gallery is empty / the user does not exist.

    With a gallery name only that folder is downloaded instead of the whole
    gallery. Exits with code 130 if the user interrupts with Ctrl+C.
    """
    print(f"User: {username}")
    if gallery:
        print(f'Gallery folder: "{gallery}"')
    print("Fetching gallery listing...")
    out_dir = output_root / username
    # Loading the manifest before fetching lets the listing stop at the
    # first fully downloaded page. --redownload-missing needs the whole
    # listing: the files it restores are recorded in the manifest, so the
    # early stop would hide them.
    manifest = DownloadManifest(out_dir) if out_dir.is_dir() else None
    meta_path = out_dir / "_metadata.json"
    previous_meta = read_json(meta_path, [])
    # Manifests written by API-only versions are keyed by UUID, which the
    # website route cannot match; the saved metadata bridges both ids.
    if manifest is not None and web is not None and previous_meta:
        migrated = manifest.adopt_web_keys(previous_meta)
        if migrated:
            print(f"  Re-keyed {migrated} previously downloaded work(s) so both "
                  "routes recognise them.")

    listing_full = full or redownload_missing
    try:
        deviations, from_web = list_gallery(client, web, username,
                                            manifest=manifest, full=listing_full,
                                            gallery=gallery)
    except UserNotFoundError as e:
        # Deactivated or non-existent profile; treat it like an empty gallery
        # so the caller reports it and, when syncing many users, moves on.
        print(f"  {e}")
        return None
    if not deviations:
        return None
    print(f"\nTotal works found: {len(deviations)}\n")

    out_dir.mkdir(parents=True, exist_ok=True)
    if manifest is None:
        manifest = DownloadManifest(out_dir)

    # Route each work: whatever the website serves in full goes through the
    # website, the rest (mature content) through the API.
    web_devs = [d for d in deviations if not needs_api(d)]
    blocked = [d for d in deviations if needs_api(d)]
    if from_web and blocked:
        blocked = resolve_via_api(client, username, blocked, deviations,
                                  manifest=manifest,
                                  redownload_missing=redownload_missing,
                                  gallery=gallery)
    jobs = [(d, WEB_SUBDIR) for d in web_devs] + [(d, API_SUBDIR) for d in blocked]
    if from_web:
        print(f"Route: {len(web_devs)} via the website ({WEB_SUBDIR}/), "
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

    counts = {"downloaded": 0, "skipped": 0, "failed": 0, "no_media": 0, "cancelled": 0}
    total = len(jobs)
    done = 0
    interrupted = False

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
                process_deviation, client, dev, out_dir, delay, manifest,
                redownload_missing, unblur,
                dest_dir=out_dir / subdir,
                session=web.session if subdir == WEB_SUBDIR else None,
                use_api=subdir == API_SUBDIR)] = dev
        try:
            for future in as_completed(futures):
                done += 1
                try:
                    status, message = future.result()
                except Exception as e:
                    status, message = "failed", f"Unexpected ERROR: {e}"
                counts[status] += 1
                print(f"[{done}/{total}] {message}")
        except KeyboardInterrupt:
            interrupted = True
            CANCEL.set()
            print("\nCtrl+C received: stopping downloads and cleaning up "
                  "partial files...")
            web_pool.shutdown(cancel_futures=True)
            api_pool.shutdown(cancel_futures=True)

    summary = (
        f"Downloaded: {counts['downloaded']} "
        f"| Skipped (already existed): {counts['skipped']} "
        f"| No file: {counts['no_media']} | Failed: {counts['failed']}"
    )
    if interrupted:
        print(f"\nInterrupted ({done} of {total} works processed). {summary}")
        print(f"Files saved to: {out_dir.resolve()}")
        print("Run the same command again to resume where it left off.")
        sys.exit(130)
    print(f"\nDone. {summary}")
    print(f"Files saved to: {out_dir.resolve()}")
    return counts
