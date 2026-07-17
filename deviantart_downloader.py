#!/usr/bin/env python3
"""
DeviantArt gallery downloader using the official public API.

Requirements:
  1. Have a DeviantArt account.
  2. Register an application at https://www.deviantart.com/developers/register
     ("confidential" type) to obtain a client_id and client_secret.
  3. pip install requests

Usage:
  cp .env.example .env   # and fill in DA_CLIENT_ID / DA_CLIENT_SECRET
  deviantart-downloader https://www.deviantart.com/username
  deviantart-downloader username

  # or passing the credentials as arguments:
  deviantart-downloader <profile_url> --client-id XXX --client-secret YYY
"""

import argparse
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse, unquote

try:
    import requests
except ImportError:
    sys.exit("Missing the 'requests' library. Install it with: pip install requests")

def load_dotenv(path: Path | None = None):
    """Load variables from a .env file without overwriting already-defined ones.

    Looks first in the current working directory and, if not found,
    next to this file (useful when running the script directly from the repo).
    """
    candidates = [path] if path else [Path.cwd() / ".env", Path(__file__).resolve().parent / ".env"]
    env_file = next((p for p in candidates if p.is_file()), None)
    if env_file is None:
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        os.environ.setdefault(key, value)


def env_int(name: str, default: int) -> int:
    """Read an integer from an environment variable, with a default value."""
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        sys.exit(f"The value of {name} must be an integer, not: {value!r}")


API_BASE = "https://www.deviantart.com/api/v1/oauth2"
TOKEN_URL = "https://www.deviantart.com/oauth2/token"
USER_AGENT = "da-gallery-downloader/1.0"
PAGE_LIMIT = 24  # maximum allowed by the API


class DeviantArtClient:
    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self._token_expiry = 0.0
        self._token_lock = threading.Lock()

    def _ensure_token(self, force: bool = False):
        with self._token_lock:
            if force or time.time() >= self._token_expiry:
                self._refresh_token()

    def _refresh_token(self):
        resp = self.session.post(
            TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            sys.exit(
                f"Error obtaining the OAuth token ({resp.status_code}): {resp.text}\n"
                "Check your client_id and client_secret."
            )
        data = resp.json()
        self.session.headers["Authorization"] = f"Bearer {data['access_token']}"
        # renew 60 s before it expires (it expires in 1 hour)
        self._token_expiry = time.time() + data.get("expires_in", 3600) - 60

    def api_get(self, endpoint: str, params: dict | None = None) -> dict:
        """GET against the API with automatic token renewal and retries."""
        self._ensure_token()

        url = f"{API_BASE}/{endpoint.lstrip('/')}"
        for attempt in range(5):
            resp = self.session.get(url, params=params, timeout=30)
            if resp.status_code == 401:
                self._ensure_token(force=True)
                continue
            if resp.status_code == 429:
                wait = 2 ** (attempt + 2)
                print(f"  Rate limit reached, waiting {wait} s...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError(f"Too many failed retries for {url}")


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


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name[:150] or "untitled"


def guess_extension(url: str) -> str:
    path = unquote(urlparse(url).path)
    ext = os.path.splitext(path)[1].lower()
    return ext if ext and len(ext) <= 5 else ".jpg"


class DownloadManifest:
    """Persistent record of already-downloaded deviations (by deviationid).

    Allows detecting duplicates across runs even if the artwork's title
    (and therefore the file name) has changed.
    """

    def __init__(self, out_dir: Path):
        self.path = out_dir / "_downloaded.json"
        self._lock = threading.Lock()
        self._entries: dict[str, str] = {}
        if self.path.is_file():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._entries = {str(k): str(v) for k, v in data.items()}
            except (json.JSONDecodeError, OSError):
                print(f"  WARNING: could not read {self.path.name}, it will be regenerated.")
        self._seed_from_existing_files(out_dir)

    def _seed_from_existing_files(self, out_dir: Path):
        """Register files downloaded by previous versions of the script
        (name suffixed with _<first 8 chars of the deviationid>)."""
        pattern = re.compile(r"_([0-9A-Fa-f]{8})$")
        for f in out_dir.iterdir():
            if not f.is_file() or f.name.startswith("_") or f.suffix == ".part":
                continue
            m = pattern.search(f.stem)
            if m:
                self._entries.setdefault(m.group(1).upper(), f.name)

    def _key(self, dev_id: str) -> str:
        return dev_id[:8].upper()

    def has(self, dev_id: str) -> bool:
        with self._lock:
            return self._key(dev_id) in self._entries

    def filename_for(self, dev_id: str) -> str | None:
        with self._lock:
            return self._entries.get(self._key(dev_id))

    def add(self, dev_id: str, filename: str):
        with self._lock:
            self._entries[self._key(dev_id)] = filename
            self._save_locked()

    def _save_locked(self):
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(self._entries, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self.path)


def fetch_gallery(client: DeviantArtClient, username: str) -> list[dict]:
    """Walk every page of gallery/all and return the deviations."""
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
        offset = data.get("next_offset") or offset + PAGE_LIMIT
    return deviations


def download_file(session: requests.Session, url: str, dest: Path) -> bool:
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with session.get(url, stream=True, timeout=60) as resp:
            resp.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1 << 16):
                    f.write(chunk)
        tmp.rename(dest)
        return True
    except Exception as e:
        tmp.unlink(missing_ok=True)
        print(f"  ERROR downloading {url}: {e}")
        return False


def process_deviation(
    client: DeviantArtClient, dev: dict, out_dir: Path, delay: float, manifest: DownloadManifest
) -> tuple[str, str]:
    """Resolve the file URL and download it. Returns (status, description)."""
    title = dev.get("title") or "untitled"
    dev_id = dev.get("deviationid", "")

    # Duplicate: already downloaded in a previous run (even if the title
    # has changed since). Checked before calling the API.
    if dev_id and manifest.has(dev_id):
        existing = manifest.filename_for(dev_id)
        if existing and (out_dir / existing).is_file():
            return "skipped", f"Already exists, skipped: {existing}"
        # The file was deleted manually: download it again.

    # 1) Prefer the original file if the author allows downloading it
    file_url = None
    if dev.get("is_downloadable"):
        try:
            dl = client.api_get(f"deviation/download/{dev_id}")
            file_url = dl.get("src")
        except Exception:
            pass  # fall back to content.src

    # 2) Otherwise, the highest publicly available resolution image
    if not file_url:
        content = dev.get("content") or {}
        file_url = content.get("src")

    if not file_url:
        # Literature, journals, etc. have no media file
        return "no_media", f"NO FILE (literature/journal): {title}"

    ext = guess_extension(file_url)
    dest = out_dir / f"{sanitize_filename(title)}_{dev_id[:8]}{ext}"

    if dest.exists():
        if dev_id:
            manifest.add(dev_id, dest.name)
        return "skipped", f"Already exists, skipped: {dest.name}"

    ok = download_file(client.session, file_url, dest)
    if delay:
        time.sleep(delay)
    if ok:
        if dev_id:
            manifest.add(dev_id, dest.name)
        return "downloaded", f"Downloaded: {dest.name}"
    return "failed", f"FAILED: {dest.name}"


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Download the full gallery of a DeviantArt profile using the official API."
    )
    parser.add_argument(
        "profile_url",
        metavar="profile",
        help="Profile URL (https://www.deviantart.com/username) or just the username",
    )
    parser.add_argument("-o", "--output", default="downloads", help="Output folder (default: downloads)")
    parser.add_argument("--client-id", default=os.environ.get("DA_CLIENT_ID"))
    parser.add_argument("--client-secret", default=os.environ.get("DA_CLIENT_SECRET"))
    parser.add_argument("--delay", type=float, default=0.5,
                        help="Pause in seconds after each download, per thread (default: 0.5)")
    parser.add_argument("-w", "--workers", type=int, default=env_int("DA_WORKERS", 4),
                        help="Simultaneous downloads (default: DA_WORKERS from .env or 4, "
                             "recommended not to exceed 8)")
    args = parser.parse_args()

    if args.workers < 1:
        sys.exit(f"The number of workers must be at least 1 (got: {args.workers}).")

    if not args.client_id or not args.client_secret:
        sys.exit(
            "Missing API credentials.\n"
            "Register at https://www.deviantart.com/developers/register and then:\n"
            "  export DA_CLIENT_ID='...'\n"
            "  export DA_CLIENT_SECRET='...'"
        )

    username = extract_username(args.profile_url)
    print(f"User: {username}")

    client = DeviantArtClient(args.client_id, args.client_secret)

    print("Fetching gallery listing...")
    deviations = fetch_gallery(client, username)
    if not deviations:
        sys.exit("The gallery is empty or the user does not exist.")
    print(f"\nTotal works found: {len(deviations)}\n")

    out_dir = Path(args.output) / username
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = DownloadManifest(out_dir)

    # Save the full metadata in case it is needed later
    with open(out_dir / "_metadata.json", "w", encoding="utf-8") as f:
        json.dump(deviations, f, ensure_ascii=False, indent=2)

    counts = {"downloaded": 0, "skipped": 0, "failed": 0, "no_media": 0}
    total = len(deviations)
    done = 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(process_deviation, client, dev, out_dir, args.delay, manifest): dev
            for dev in deviations
        }
        for future in as_completed(futures):
            done += 1
            try:
                status, message = future.result()
            except Exception as e:
                status, message = "failed", f"Unexpected ERROR: {e}"
            counts[status] += 1
            print(f"[{done}/{total}] {message}")

    downloaded, skipped, failed, no_media = (
        counts["downloaded"], counts["skipped"], counts["failed"], counts["no_media"]
    )
    print(
        f"\nDone. Downloaded: {downloaded} | Skipped (already existed): {skipped} "
        f"| No file: {no_media} | Failed: {failed}"
    )
    print(f"Files saved to: {out_dir.resolve()}")


if __name__ == "__main__":
    main()
