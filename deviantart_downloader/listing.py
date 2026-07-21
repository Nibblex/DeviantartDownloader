"""Walking a gallery listing, over either route, and pairing the two up."""

from .api import DeviantArtClient
from .constants import PAGE_LIMIT, WEB_PAGE_LIMIT
from .manifest import DownloadManifest
from .naming import deviation_key
from .web import WebClient, WebError, normalize_web_deviation


def fetch_gallery(
    client: DeviantArtClient, username: str, *,
    manifest: DownloadManifest | None = None, full: bool = False,
) -> list[dict]:
    """Walk the pages of gallery/all (newest first) and return the deviations.

    When a manifest is given and full is False, pagination stops after the
    first page whose works are all already recorded: everything older was
    listed by a previous run. Failed downloads are never in the manifest,
    so they keep the walk going until they succeed.
    """
    deviations = []
    offset = 0
    while True:
        data = client.api_get(
            "gallery/all",
            params={
                "username": username,
                "offset": offset,
                "limit": PAGE_LIMIT,
                "mature_content": "true",
            },
        )
        results = data.get("results", [])
        deviations.extend(results)
        print(f"  Page at offset {offset}: {len(results)} works (total: {len(deviations)})")
        if not data.get("has_more"):
            break
        if page_fully_downloaded(results, manifest, full):
            print("  Every work on this page was already downloaded; stopping the "
                  "listing early (pass --full to walk the whole gallery).")
            break
        offset = data.get("next_offset") or offset + PAGE_LIMIT
    return deviations


def page_fully_downloaded(results: list[dict], manifest: "DownloadManifest | None",
                          full: bool) -> bool:
    """True when every work on a listing page is already in the manifest."""
    if manifest is None or full or not results:
        return False
    return all((key := deviation_key(d)) and manifest.has(key) for d in results)


def fetch_gallery_web(
    web: WebClient, username: str, *,
    manifest: DownloadManifest | None = None, full: bool = False,
) -> list[dict]:
    """Same walk as fetch_gallery, over the website listing and without OAuth.

    Entries come back normalized to the API's shape, so everything downstream
    treats both routes alike.
    """
    deviations = []
    offset = 0
    while True:
        data = web.gallery_page(username, offset, WEB_PAGE_LIMIT)
        results = [normalize_web_deviation(item) for item in data.get("results", [])]
        deviations.extend(results)
        print(f"  Page at offset {offset}: {len(results)} works (total: {len(deviations)})")
        if not data.get("hasMore"):
            break
        if page_fully_downloaded(results, manifest, full):
            print("  Every work on this page was already downloaded; stopping the "
                  "listing early (pass --full to walk the whole gallery).")
            break
        offset = data.get("nextOffset") or offset + WEB_PAGE_LIMIT
    return deviations


def list_gallery(
    client: DeviantArtClient, web: WebClient | None, username: str, *,
    manifest: DownloadManifest | None, full: bool,
) -> tuple[list[dict], bool]:
    """Fetch the gallery listing, preferring the website over the API.

    The website route costs no API quota at all; if it is unavailable (the
    endpoint changed, the profile is hidden, ...) the API takes over. Returns
    the works and whether they came from the website.
    """
    if web is not None:
        try:
            return fetch_gallery_web(web, username, manifest=manifest, full=full), True
        except WebError as e:
            print(f"  Website listing unavailable ({e}); falling back to the API.")
    return fetch_gallery(client, username, manifest=manifest, full=full), False


def resolve_via_api(
    client: DeviantArtClient, username: str, blocked: list[dict], *,
    manifest: DownloadManifest, full: bool, redownload_missing: bool,
) -> list[dict]:
    """Look up the API entries of the works the website only serves blurred.

    The API is keyed by UUID, which the website listing does not carry, so the
    works are matched through the gallery listing. That listing is only walked
    when at least one blocked work still has to be downloaded, which keeps an
    incremental sync of an all-ages gallery entirely free of API calls.
    """
    pending = [d for d in blocked
               if redownload_missing or not manifest.has(deviation_key(d))]
    if not pending:
        return []
    print(f"\n{len(pending)} mature work(s) need the API; fetching the API listing...")
    api_listing = fetch_gallery(client, username, manifest=manifest, full=full)
    index = {deviation_key(d): d for d in api_listing}
    resolved, missing = [], 0
    for dev in pending:
        match = index.get(deviation_key(dev))
        if match is not None:
            resolved.append(match)
        else:
            missing += 1
    if missing:
        print(f"  WARNING: {missing} mature work(s) were not in the API listing "
              "(pass --full to walk it whole).")
    return resolved
