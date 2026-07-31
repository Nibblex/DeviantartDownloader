"""Resolving a work to a file URL and writing it to disk."""

from pathlib import Path

import requests

from . import literature
from .api import DeviantArtClient
from .constants import API_SUBDIR, CANCEL, RESUME, wait_if_paused
from .literature import KIND_HTML, KIND_TEXT, classify_web_html, is_text_work
from .manifest import DownloadManifest
from .naming import (clamp_wixmp_blur, content_src, deviation_key,
                     deviation_suffix, deviation_title, guess_extension,
                     is_blurred, sanitize_filename, unblur_wixmp_url,
                     username_from_url)
from .web import WebClient, WebError


def remote_size(session: requests.Session, url: str) -> int | None:
    """What the CDN says a URL weighs, or None when it will not say."""
    try:
        resp = session.head(url, allow_redirects=True, timeout=30)
        if resp.status_code == 200:
            return int(resp.headers["Content-Length"])
    except (requests.RequestException, KeyError, ValueError):
        pass
    return None


def _is_the_original(session: requests.Session, offered: str,
                     original_size) -> bool:
    """True when the URL the listing already carries weighs what the original does.

    The API charges a request per work to hand out the original file, and for
    most works it hands back what content.src was already serving: the fullview
    is only re-encoded when the original is too large for it. The listing says
    how many bytes the original has (download_filesize) and the CDN says how
    many the fullview has, so the two can be compared before spending anything
    -- the head request costs no API quota.

    Every uncertainty answers False, which spends the request: an unknown size,
    or a CDN that will not say. Being wrong that way costs a request; the other
    way would silently save the fullview as if it were the original.

    A blur is the one uncertainty answered without asking. The placeholder is a
    different, smaller file, so it could never weigh what the original does, and
    the head request would be a round trip spent to learn nothing -- on exactly
    the works a logged-out run is full of.
    """
    if not offered or not isinstance(original_size, int) or original_size <= 0:
        return False
    if is_blurred(offered):
        return False
    return remote_size(session, offered) == original_size


# How many times a transfer is picked up again after the connection failed.
# A pause is not one of these: it is not a failure and can happen all day.
STREAM_RETRIES = 2


def _receive(resp, tmp: Path, append: bool) -> str:
    """Write a response body to the partial file; say why it stopped.

    "done" when the body ran out, "cancelled" for 'q', and "paused" for 'p' --
    which returns rather than blocking, so the socket is let go of instead of
    being held open for however long the pause lasts. The chunk in hand is
    dropped with it and fetched again by the request that picks up from what is
    on disk; both checks come before the write so that a pause always leaves
    something still to fetch.
    """
    with open(tmp, "ab" if append else "wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 16):
            if CANCEL.is_set():
                return "cancelled"
            if not RESUME.is_set():
                return "paused"
            f.write(chunk)
    return "done"


def download_file(
    session: requests.Session, url: str, dest: Path,
    fallback_url: str | None = None,
) -> bool:
    """Fetch a URL into dest, picking up again what interrupted it.

    A pause used to be taken mid-transfer, with the response half-read and the
    socket held open for as long as the user was away; whatever closed it first
    -- the CDN, a laptop going to sleep, a router -- ended the work as a failure
    and threw away every byte of it. The transfer is now let go of instead, and
    what is already on disk is continued with a Range request, which the CDN
    answers with a 206. A connection lost mid-transfer is picked up the same
    way, a couple of times, before it counts as a failure.

    The partial file is only ever continued within this call: a leftover from an
    earlier run may well be a different URL's, and appending to that would
    splice two files into one.
    """
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.unlink(missing_ok=True)

    def failed(reason) -> bool:
        tmp.unlink(missing_ok=True)
        print(f"  ERROR downloading {url}: {reason}")
        return False

    retries = STREAM_RETRIES
    while True:
        have = tmp.stat().st_size if tmp.is_file() else 0
        try:
            with session.get(url, stream=True, timeout=60,
                             headers={"Range": f"bytes={have}-"} if have else {}
                             ) as resp:
                if resp.status_code == 403 and fallback_url:
                    # The unblurred URL was rejected (token pinned to the
                    # blurred transformation); keep the blurred preview.
                    print(f"  Unblur rejected by the CDN, keeping the blurred preview: {dest.name}")
                    return download_file(session, fallback_url, dest)
                resp.raise_for_status()
                # A server that ignores the Range sends the whole file back:
                # appending that to what is here would splice one onto the other.
                stopped = _receive(resp, tmp, append=bool(have) and
                                   resp.status_code == 206)
        except requests.HTTPError as e:
            # The server answered, and the answer was no: a 404, a 403 with no
            # blurred fallback. Asking again would only spend the request.
            return failed(e)
        except requests.RequestException as e:
            # The transport gave way instead -- a connection closed, a read
            # timed out -- which is exactly what picking it up again is for.
            retries -= 1
            if retries < 0 or CANCEL.is_set():
                return failed(e)
            print(f"  Connection lost {have} bytes into {dest.name}, "
                  f"picking it up again: {e}")
            continue
        except Exception as e:
            return failed(e)
        if stopped == "done":
            tmp.rename(dest)
            return True
        if stopped == "cancelled":
            tmp.unlink(missing_ok=True)
            return False
        wait_if_paused()          # 'p': waited out with no socket held open
        if CANCEL.is_set():       # 'q' pressed while paused
            tmp.unlink(missing_ok=True)
            return False


def resolve_literature(dev: dict, client: DeviantArtClient,
                       web: WebClient | None, use_api: bool) -> tuple[str, str] | None:
    """Best-effort body of a literature/journal work as (kind, payload), else None.

    The website route reads the whole body off the deviation page for no API
    quota; the API's content endpoint serves the older HTML-format works but
    comes back empty for the current editor, so both fall back to the excerpt
    the listing already carries (which is the full text for short works and
    truncated for long ones). The kind is left unrendered so the caller can
    produce either plain text or HTML from the same payload.
    """
    dev_id = dev.get("deviationid", "")
    if web is not None and not use_api and str(dev_id).isdigit():
        try:
            text_content = web.deviation_text(dev_id, username_from_url(dev.get("url") or ""))
        except WebError:
            text_content = None
        if text_content:
            classified = classify_web_html(text_content.get("html"))
            if classified:
                return classified
            if text_content.get("excerpt"):
                return KIND_TEXT, text_content["excerpt"]

    if use_api and dev_id:
        try:
            content = client.api_get("deviation/content",
                                     params={"deviationid": dev_id, "mature_content": "true"})
            markup = content.get("html")
            if markup and isinstance(markup, str) and markup.strip():
                return KIND_HTML, markup
        except Exception:
            pass  # fall back to the excerpt from the listing

    excerpt = dev.get("excerpt") or (dev.get("text_content") or {}).get("excerpt")
    return (KIND_TEXT, excerpt) if excerpt else None


def _write_text(kind: str, payload: str, text_format: str, title: str, dev: dict,
                out_dir: Path, dest_dir: Path, manifest: DownloadManifest,
                key: str) -> tuple[str, str]:
    """Write a text work to a .txt/.html file, mirroring the media download path."""
    body = literature.render(kind, payload, text_format)
    if text_format == "html":
        content, ext = literature.html_document(title, body), ".html"
    else:
        content, ext = body + "\n", ".txt"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{sanitize_filename(title)}_{deviation_suffix(dev)}{ext}"
    rel = dest.relative_to(out_dir).as_posix()
    if dest.exists():
        if key:
            manifest.add(key, rel)
        return "skipped", f"Already exists, skipped: {rel}"
    if CANCEL.is_set():
        return "cancelled", f"Cancelled: {title}"
    dest.write_text(content, encoding="utf-8")
    if key:
        manifest.add(key, rel)
    return "downloaded", f"Downloaded (text): {rel}"


def process_deviation(
    client: DeviantArtClient, dev: dict, out_dir: Path,
    manifest: DownloadManifest, redownload_missing: bool = False,
    unblur: bool = False, *, redownload_blurred: bool = False,
    dest_dir: Path | None = None,
    session: requests.Session | None = None, use_api: bool = True,
    web: WebClient | None = None, text_format: str = "txt",
) -> tuple[str, str]:
    """Resolve the file URL and download it. Returns (status, description).

    The file lands in dest_dir (the gallery folder itself by default) and is
    recorded in the manifest under its path relative to out_dir. With use_api
    False no API call is made, so the work is resolved from the listing alone.
    """
    title = deviation_title(dev)
    dev_id = dev.get("deviationid", "")
    key = deviation_key(dev)
    dest_dir = dest_dir or out_dir
    session = session or client.session

    wait_if_paused()                      # hold queued works while paused
    if CANCEL.is_set():
        return "cancelled", f"Cancelled: {title}"

    # Duplicate: already downloaded in a previous run (even if the title
    # has changed since). Checked before calling the API. The manifest is
    # authoritative: a deleted file is not downloaded again unless
    # --redownload-missing is passed.
    replacing = None                  # the file --redownload-blurred may replace
    if key and manifest.has(key):
        existing = manifest.filename_for(key)
        if existing and (out_dir / existing).is_file():
            # --redownload-blurred replaces the blurred placeholder a logged-out
            # run settled for. Only the API route ever handed one back, and only
            # if the API is offering something better now is it worth the bytes.
            offered = content_src(dev)
            if not (redownload_blurred and existing.startswith(f"{API_SUBDIR}/")):
                return "skipped", f"Already exists, skipped: {existing}"
            if is_blurred(offered):
                return "skipped", f"Still only served blurred, kept: {existing}"
            replacing = out_dir / existing
        elif not redownload_missing:
            return "skipped", f"Deleted locally, skipped: {existing or title}"
        # --redownload-missing: restore the manually deleted file.

    # 1) Prefer the original file if the author allows downloading it.
    #
    # The API is the only source of originals: the website's deviation page
    # carries a download URL, but it answers 404 to anyone without a logged-in
    # browser session, which the OAuth flow does not provide, and works served
    # blurred are not offered one there at all. So content.src (the derived
    # fullview) is the fallback -- except that it often *is* the original file
    # already, which is worth finding out before paying for the answer.
    file_url = None
    fallback_url = None
    offered = content_src(dev)
    if use_api and dev.get("is_downloadable") and not _is_the_original(
            session, offered, dev.get("download_filesize")):
        try:
            dl = client.api_get(f"deviation/download/{dev_id}")
            file_url = dl.get("src")
        except Exception:
            pass  # fall back to content.src

    # 2) Otherwise, the highest publicly available resolution image
    if not file_url:
        file_url = content_src(dev)
        if file_url and unblur:
            unblurred = unblur_wixmp_url(file_url)
            if unblurred != file_url:
                fallback_url = file_url
                file_url = unblurred

    if not file_url:
        # Literature and journals have no media file; save their text instead.
        if is_text_work(dev):
            resolved = resolve_literature(dev, client, web, use_api)
            if resolved is not None:
                kind, payload = resolved
                return _write_text(kind, payload, text_format, title, dev, out_dir,
                                   dest_dir, manifest, key)
        return "no_media", f"NO FILE (no text or media): {title}"

    # Whichever route produced it, the URL may carry a blur the CDN refuses.
    file_url = clamp_wixmp_blur(file_url)
    if fallback_url:
        fallback_url = clamp_wixmp_blur(fallback_url)

    # The unblurred image is a different file from the blurred placeholder, so a
    # matching size means this copy is already the good one and the bytes would
    # be spent for nothing. A size the CDN will not report re-fetches, which is
    # the safe way to be wrong.
    if replacing is not None and remote_size(session, file_url) == replacing.stat().st_size:
        return "skipped", f"Already unblurred, kept: {existing}"

    ext = guess_extension(file_url)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{sanitize_filename(title)}_{deviation_suffix(dev)}{ext}"
    rel = dest.relative_to(out_dir).as_posix()

    if dest.exists() and replacing is None:
        if key:
            manifest.add(key, rel)
        return "skipped", f"Already exists, skipped: {rel}"

    ok = download_file(session, file_url, dest, fallback_url)
    if ok:
        if replacing is not None:
            # Counted apart from an ordinary download: after a repair pass the
            # number worth knowing is how many blurred copies actually changed,
            # which "Downloaded" alone would bury among works that were simply new.
            if replacing != dest:
                # The clean image can resolve to a different extension than
                # the blurred one did; leaving the old file behind would keep
                # the blur on disk under a name nothing points at any more.
                replacing.unlink(missing_ok=True)
            if key:
                manifest.add(key, rel)
            return "replaced", f"Replaced blurred copy: {rel}"
        if key:
            manifest.add(key, rel)
        return "downloaded", f"Downloaded: {rel}"
    if CANCEL.is_set():
        return "cancelled", f"Cancelled: {title}"
    return "failed", f"FAILED: {rel}"
