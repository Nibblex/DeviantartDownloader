"""Walking a gallery listing, over either route, and pairing the two up."""

from .api import DeviantArtClient
from .constants import CANCEL, PAGE_LIMIT, WEB_PAGE_LIMIT, wait_if_paused
from .manifest import DownloadManifest
from .naming import deviation_key
from .web import WebClient, WebError, normalize_web_deviation


class GalleryNotFoundError(RuntimeError):
    """The user has no gallery folder with the requested name."""

    def __init__(self, username: str, name: str, available: list):
        self.username = username
        self.name = name
        self.available = [a for a in available if a]
        shown = ", ".join(f'"{a}"' for a in self.available) or "none"
        super().__init__(
            f'{username} has no gallery folder named "{name}". '
            f"Available folders: {shown}."
        )


def _match_folder_id(folders: list[dict], name: str, key: str):
    """The id under `key` of the folder whose name matches, else None."""
    wanted = name.strip().casefold()
    for folder in folders:
        if str(folder.get("name") or "").strip().casefold() == wanted:
            return folder.get(key)
    return None


def resolve_folder_web(web: WebClient, username: str, name: str):
    """The numeric folderId of a named gallery on the website route."""
    folders = web.list_folders(username)  # WebError if the request itself fails
    folder_id = _match_folder_id(folders, name, "folderId")
    if folder_id is None:
        raise GalleryNotFoundError(username, name,
                                   [f.get("name") for f in folders])
    return folder_id


def fetch_api_folders(client: DeviantArtClient, username: str, *,
                      calculate_size: bool = False) -> list[dict]:
    """Every gallery folder of a user, over the API (name + folderid UUID).

    With calculate_size the API also fills each folder's item count (`size`),
    at the cost of a heavier query; it is left off for plain name resolution.
    """
    folders, offset = [], 0
    while True:
        params = {"username": username, "offset": offset, "limit": PAGE_LIMIT,
                  "mature_content": "true"}
        if calculate_size:
            params["calculate_size"] = "true"
        data = client.api_get("gallery/folders", params=params)
        folders.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        offset = data.get("next_offset") or offset + PAGE_LIMIT
    return folders


def resolve_folder_api(client: DeviantArtClient, username: str, name: str) -> str:
    """The folderid UUID of a named gallery on the API route."""
    folders = fetch_api_folders(client, username)
    folder_id = _match_folder_id(folders, name, "folderid")
    if folder_id is None:
        raise GalleryNotFoundError(username, name,
                                   [f.get("name") for f in folders])
    return folder_id


def fetch_gallery(
    client: DeviantArtClient, username: str, *, folder: str | None = None,
    manifest: DownloadManifest | None = None, full: bool = False,
) -> list[dict]:
    """Walk the pages of a gallery (newest first) and return the deviations.

    With folder None the whole gallery (gallery/all) is walked; otherwise only
    that folder (its folderid UUID). When a manifest is given and full is
    False, pagination stops after the first page whose works are all already
    recorded: everything older was listed by a previous run. Failed downloads
    are never in the manifest, so they keep the walk going until they succeed.
    """
    endpoint = f"gallery/{folder}" if folder else "gallery/all"
    deviations = []
    offset = 0
    while True:
        wait_if_paused()
        if CANCEL.is_set():                   # 'q' during the listing
            break
        data = client.api_get(
            endpoint,
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
    web: WebClient, username: str, *, folderid: object = None,
    manifest: DownloadManifest | None = None, full: bool = False,
) -> list[dict]:
    """Same walk as fetch_gallery, over the website listing and without OAuth.

    With folderid None the whole gallery is walked; otherwise only that folder
    (its numeric folderId). Entries come back normalized to the API's shape, so
    everything downstream treats both routes alike.
    """
    deviations = []
    offset = 0
    while True:
        wait_if_paused()
        if CANCEL.is_set():                   # 'q' during the listing
            break
        data = web.gallery_page(username, offset, WEB_PAGE_LIMIT, folderid=folderid)
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
    manifest: DownloadManifest | None, full: bool, gallery: str | None = None,
) -> tuple[list[dict], bool]:
    """Fetch the gallery listing, preferring the website over the API.

    The website route costs no API quota at all; if it is unavailable (the
    endpoint changed, the profile is hidden, ...) the API takes over. With a
    gallery name only that folder is listed. Returns the works and whether they
    came from the website.

    A GalleryNotFoundError (the folder listing worked but no name matched) is
    not a route failure and is left to propagate rather than falling back.
    """
    if web is not None:
        try:
            folderid = resolve_folder_web(web, username, gallery) if gallery else None
            return fetch_gallery_web(web, username, folderid=folderid,
                                     manifest=manifest, full=full), True
        except WebError as e:
            print(f"  Website listing unavailable ({e}); falling back to the API.")
    folder = resolve_folder_api(client, username, gallery) if gallery else None
    return fetch_gallery(client, username, folder=folder,
                         manifest=manifest, full=full), False


def _api_page(client: DeviantArtClient, endpoint: str, username: str,
              offset: int) -> dict:
    """One page of the API gallery listing at an exact offset."""
    return client.api_get(endpoint, params={
        "username": username, "offset": offset,
        "limit": PAGE_LIMIT, "mature_content": "true",
    })


def resolve_via_api(
    client: DeviantArtClient, username: str, blocked: list[dict],
    ordered: list[dict] | None = None, *,
    manifest: DownloadManifest, redownload_missing: bool,
    gallery: str | None = None,
) -> list[dict]:
    """Look up the API entries of the works the website only serves blurred.

    The API is keyed by UUID, which the website listing does not carry, so the
    works are matched through the gallery listing (the same folder, when a
    gallery name is given). That listing is only walked when at least one
    blocked work still has to be downloaded, which keeps an incremental sync of
    an all-ages gallery entirely free of API calls.

    Both routes list a gallery newest-first in the same order, so each blocked
    work's position in `ordered` (the full website listing) points at the API
    page that should hold it: only those pages are fetched, instead of walking
    the whole gallery. Should the two orders drift apart, any work the targeted
    pages missed is filled in by walking the remaining pages, stopping as soon
    as every pending work is found.
    """
    pending = [d for d in blocked
               if redownload_missing or not manifest.has(deviation_key(d))]
    if not pending:
        return []
    print(f"\n{len(pending)} mature work(s) need the API; fetching the pages "
          "that hold them...")
    folder = resolve_folder_api(client, username, gallery) if gallery else None
    endpoint = f"gallery/{folder}" if folder else "gallery/all"

    index: dict[str, dict] = {}       # deviation key -> API entry
    fetched: set[int] = set()         # page offsets already retrieved
    terminal: list[int] = []          # offsets whose page reported no more

    def absorb(offset: int) -> dict:
        """Fetch one page (once), index its works and report the progress."""
        data = _api_page(client, endpoint, username, offset)
        fetched.add(offset)
        if not data.get("has_more"):
            terminal.append(offset)
        results = data.get("results", [])
        for r in results:
            index.setdefault(deviation_key(r), r)
        matched = sum(1 for d in pending if deviation_key(d) in index)
        print(f"  Page at offset {offset}: {len(results)} works "
              f"({matched}/{len(pending)} matched)")
        return data

    def missing() -> list[dict]:
        return [d for d in pending if deviation_key(d) not in index]

    # Pass 1: the pages the website positions point at.
    position = {deviation_key(d): i for i, d in enumerate(ordered or [])}
    wanted = sorted({(position[k] // PAGE_LIMIT) * PAGE_LIMIT
                     for d in pending if (k := deviation_key(d)) in position})
    for off in wanted:
        wait_if_paused()
        if CANCEL.is_set():                   # 'q' during the lookup
            return []
        absorb(off)

    # Pass 2: fill the gaps left by any drift between the two orderings, walking
    # the still-unseen pages until every pending work turns up or the listing
    # ends. A page that reported no more works marks the end: nothing past the
    # earliest such offset exists, so the walk never reaches for it.
    offset = 0
    while missing():
        wait_if_paused()
        if CANCEL.is_set():                   # 'q' during the lookup
            return []
        if terminal and offset > min(terminal):
            break
        if offset in fetched:
            offset += PAGE_LIMIT
            continue
        data = absorb(offset)
        offset = data.get("next_offset") or offset + PAGE_LIMIT

    resolved = [index[k] for d in pending if (k := deviation_key(d)) in index]
    if len(resolved) < len(pending):
        print(f"  WARNING: {len(pending) - len(resolved)} mature work(s) were "
              "not in the API listing.")
    return resolved
