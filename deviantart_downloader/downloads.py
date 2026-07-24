"""Resolving a work to a file URL and writing it to disk."""

from pathlib import Path

import requests

from . import literature
from .api import DeviantArtClient
from .constants import CANCEL, wait_if_paused
from .literature import KIND_HTML, KIND_TEXT, classify_web_html, is_text_work
from .manifest import DownloadManifest
from .naming import (deviation_key, deviation_suffix, guess_extension,
                     sanitize_filename, unblur_wixmp_url, username_from_url)
from .web import WebClient, WebError


def download_file(
    session: requests.Session, url: str, dest: Path,
    fallback_url: str | None = None,
) -> bool:
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with session.get(url, stream=True, timeout=60) as resp:
            if resp.status_code == 403 and fallback_url:
                # The unblurred URL was rejected (token pinned to the
                # blurred transformation); keep the blurred preview.
                print(f"  Unblur rejected by the CDN, keeping the blurred preview: {dest.name}")
                return download_file(session, fallback_url, dest)
            resp.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1 << 16):
                    wait_if_paused()      # pauses mid-download when the user asks
                    if CANCEL.is_set():
                        tmp.unlink(missing_ok=True)
                        return False
                    f.write(chunk)
        tmp.rename(dest)
        return True
    except Exception as e:
        tmp.unlink(missing_ok=True)
        print(f"  ERROR downloading {url}: {e}")
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
                key: str, delay: float, use_api: bool) -> tuple[str, str]:
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
    if delay and use_api:
        CANCEL.wait(delay)
    if key:
        manifest.add(key, rel)
    return "downloaded", f"Downloaded (text): {rel}"


def process_deviation(
    client: DeviantArtClient, dev: dict, out_dir: Path, delay: float,
    manifest: DownloadManifest, redownload_missing: bool = False,
    unblur: bool = False, *, dest_dir: Path | None = None,
    session: requests.Session | None = None, use_api: bool = True,
    web: WebClient | None = None, text_format: str = "txt",
) -> tuple[str, str]:
    """Resolve the file URL and download it. Returns (status, description).

    The file lands in dest_dir (the gallery folder itself by default) and is
    recorded in the manifest under its path relative to out_dir. With use_api
    False no API call is made, so the work is resolved from the listing alone.
    """
    title = dev.get("title") or "untitled"
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
    if key and manifest.has(key):
        existing = manifest.filename_for(key)
        if existing and (out_dir / existing).is_file():
            return "skipped", f"Already exists, skipped: {existing}"
        if not redownload_missing:
            return "skipped", f"Deleted locally, skipped: {existing or title}"
        # --redownload-missing: restore the manually deleted file.

    # 1) Prefer the original file if the author allows downloading it
    file_url = None
    fallback_url = None
    if use_api and dev.get("is_downloadable"):
        try:
            dl = client.api_get(f"deviation/download/{dev_id}")
            file_url = dl.get("src")
        except Exception:
            pass  # fall back to content.src

    # 2) Otherwise, the highest publicly available resolution image
    if not file_url:
        content = dev.get("content") or {}
        file_url = content.get("src")
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
                                   dest_dir, manifest, key, delay, use_api)
        return "no_media", f"NO FILE (no text or media): {title}"

    ext = guess_extension(file_url)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{sanitize_filename(title)}_{deviation_suffix(dev)}{ext}"
    rel = dest.relative_to(out_dir).as_posix()

    if dest.exists():
        if key:
            manifest.add(key, rel)
        return "skipped", f"Already exists, skipped: {rel}"

    ok = download_file(session, file_url, dest, fallback_url)
    if delay and use_api:
        # Only the API route is throttled; the website route costs no quota.
        CANCEL.wait(delay)  # like time.sleep(delay), but wakes up on Ctrl+C
    if ok:
        if key:
            manifest.add(key, rel)
        return "downloaded", f"Downloaded: {rel}"
    if CANCEL.is_set():
        return "cancelled", f"Cancelled: {title}"
    return "failed", f"FAILED: {rel}"
