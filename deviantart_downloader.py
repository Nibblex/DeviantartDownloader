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

  # with no profile, re-sync every user already present in the output folder:
  deviantart-downloader

  # or passing the credentials as arguments:
  deviantart-downloader <profile_url> --client-id XXX --client-secret YYY

  # log in with your account so mature works are served unblurred
  # (requires whitelisting http://127.0.0.1:8721/callback in your app):
  deviantart-downloader --login
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
from urllib.parse import urlparse, unquote, urlencode, parse_qs

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


def env_bool(name: str, default: bool) -> bool:
    """Read a boolean from an environment variable, with a default value."""
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    sys.exit(f"The value of {name} must be a boolean (true/false), not: {value!r}")


API_BASE = "https://www.deviantart.com/api/v1/oauth2"
TOKEN_URL = "https://www.deviantart.com/oauth2/token"
AUTH_URL = "https://www.deviantart.com/oauth2/authorize"
REDIRECT_PORT = 8721
REDIRECT_URI = f"http://127.0.0.1:{REDIRECT_PORT}/callback"
TOKEN_FILE = Path.home() / ".config" / "deviantart-downloader" / "token.json"
USER_AGENT = "da-gallery-downloader/1.0"
PAGE_LIMIT = 24  # maximum allowed by the API

# Set on Ctrl+C so worker threads abort in-progress downloads promptly.
CANCEL = threading.Event()


class ApiError(RuntimeError):
    """The API kept failing after exhausting every retry."""


class DeviantArtClient:
    def __init__(self, client_id: str, client_secret: str, token_file: Path = TOKEN_FILE):
        self.client_id = client_id
        self.client_secret = client_secret
        self.token_file = token_file
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self._token_expiry = 0.0
        self._token_lock = threading.Lock()

    @property
    def user_mode(self) -> bool:
        """True when a user session saved by --login will be used."""
        return self.token_file.is_file()

    def _ensure_token(self, force: bool = False):
        with self._token_lock:
            if force or time.time() >= self._token_expiry:
                self._refresh_token()

    def _token_request(self, grant: dict, error_hint: str) -> dict:
        resp = self.session.post(
            TOKEN_URL,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                **grant,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            sys.exit(
                f"Error obtaining the OAuth token ({resp.status_code}): {resp.text}\n"
                + error_hint
            )
        return resp.json()

    def _apply_token(self, data: dict):
        self.session.headers["Authorization"] = f"Bearer {data['access_token']}"
        # renew 60 s before it expires (it expires in 1 hour)
        self._token_expiry = time.time() + data.get("expires_in", 3600) - 60

    def _refresh_token(self):
        if self.user_mode:
            try:
                saved = json.loads(self.token_file.read_text(encoding="utf-8"))
                refresh = saved["refresh_token"]
            except (OSError, ValueError, KeyError):
                sys.exit(f"Could not read {self.token_file}; log in again with --login.")
            data = self._token_request(
                {"grant_type": "refresh_token", "refresh_token": refresh},
                "The saved session is no longer valid; log in again with --login.",
            )
            self.save_user_token(data)
        else:
            data = self._token_request(
                {"grant_type": "client_credentials"},
                "Check your client_id and client_secret.",
            )
            self._apply_token(data)

    def save_user_token(self, data: dict):
        """Persist the refresh token (DeviantArt rotates them on every use)."""
        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        self.token_file.write_text(
            json.dumps({"refresh_token": data["refresh_token"]}, indent=2),
            encoding="utf-8",
        )
        try:
            self.token_file.chmod(0o600)
        except OSError:
            pass
        self._apply_token(data)

    def api_get(self, endpoint: str, params: dict | None = None) -> dict:
        """GET against the API with automatic token renewal and retries."""
        self._ensure_token()

        url = f"{API_BASE}/{endpoint.lstrip('/')}"
        max_attempts = 10
        backoff = 4
        for attempt in range(max_attempts):
            resp = self.session.get(url, params=params, timeout=30)
            if resp.status_code == 401:
                self._ensure_token(force=True)
                continue
            if resp.status_code == 429:
                if attempt + 1 == max_attempts:
                    break
                retry_after = resp.headers.get("Retry-After", "")
                wait = int(retry_after) if retry_after.isdigit() else backoff
                backoff = min(backoff * 2, 300)
                print(f"  Rate limit reached, waiting {wait} s...")
                if CANCEL.wait(wait):
                    raise RuntimeError("Cancelled by the user")
                continue
            resp.raise_for_status()
            return resp.json()
        raise ApiError(
            f"DeviantArt kept rate-limiting {url} after every retry "
            "(the block usually clears after a few minutes).\n"
            "Try again later, and consider lowering DA_WORKERS to 4 or less "
            "if it keeps happening."
        )


def login(client: DeviantArtClient):
    """Interactive OAuth login (Authorization Code grant).

    Opens the browser so the user authorizes the app, receives the code on
    a local HTTP server and saves the refresh token for future runs. With a
    user session, mature deviations are served unblurred (as long as the
    account has mature content enabled in its settings).
    """
    import base64
    import hashlib
    import secrets
    import webbrowser
    from http.server import BaseHTTPRequestHandler, HTTPServer

    state = secrets.token_urlsafe(16)
    # PKCE (required by DeviantArt): S256 challenge derived from a one-off verifier
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    result: dict[str, str] = {}

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self):
            params = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
            ok = params.get("state") == state and "code" in params
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<h2>Login complete, you can close this tab.</h2>" if ok
                else b"<h2>Login failed, check the terminal.</h2>"
            )
            result.update(params)

        def log_message(self, *args):
            pass

    auth_url = AUTH_URL + "?" + urlencode({
        "response_type": "code",
        "client_id": client.client_id,
        "redirect_uri": REDIRECT_URI,
        "scope": "browse",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    })
    print(
        "A browser window will open so you can authorize the application.\n"
        f"If it does not open, visit:\n  {auth_url}\n\n"
        f"NOTE: the app must list {REDIRECT_URI} in its 'OAuth2 Redirect URI\n"
        "Whitelist' (https://www.deviantart.com/developers/apps).\n"
    )
    server = HTTPServer(("127.0.0.1", REDIRECT_PORT), Callback)
    try:
        webbrowser.open(auth_url)
        while not result:
            server.handle_request()
    finally:
        server.server_close()

    if result.get("state") != state or "code" not in result:
        sys.exit(f"Authorization failed: {result.get('error_description') or result}")

    data = client._token_request(
        {
            "grant_type": "authorization_code",
            "code": result["code"],
            "redirect_uri": REDIRECT_URI,
            "code_verifier": code_verifier,
        },
        "Could not exchange the authorization code.",
    )
    client.save_user_token(data)
    print("Login successful; the session was saved for future runs.\n")


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


def process_deviation(
    client: DeviantArtClient, dev: dict, out_dir: Path, delay: float,
    manifest: DownloadManifest, redownload_missing: bool = False,
    unblur: bool = False
) -> tuple[str, str]:
    """Resolve the file URL and download it. Returns (status, description)."""
    title = dev.get("title") or "untitled"
    dev_id = dev.get("deviationid", "")

    if CANCEL.is_set():
        return "cancelled", f"Cancelled: {title}"

    # Duplicate: already downloaded in a previous run (even if the title
    # has changed since). Checked before calling the API. The manifest is
    # authoritative: a deleted file is not downloaded again unless
    # --redownload-missing is passed.
    if dev_id and manifest.has(dev_id):
        existing = manifest.filename_for(dev_id)
        if existing and (out_dir / existing).is_file():
            return "skipped", f"Already exists, skipped: {existing}"
        if not redownload_missing:
            return "skipped", f"Deleted locally, skipped: {existing or title}"
        # --redownload-missing: restore the manually deleted file.

    # 1) Prefer the original file if the author allows downloading it
    file_url = None
    fallback_url = None
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
        if file_url and unblur:
            unblurred = unblur_wixmp_url(file_url)
            if unblurred != file_url:
                fallback_url = file_url
                file_url = unblurred

    if not file_url:
        # Literature, journals, etc. have no media file
        return "no_media", f"NO FILE (literature/journal): {title}"

    ext = guess_extension(file_url)
    dest = out_dir / f"{sanitize_filename(title)}_{dev_id[:8]}{ext}"

    if dest.exists():
        if dev_id:
            manifest.add(dev_id, dest.name)
        return "skipped", f"Already exists, skipped: {dest.name}"

    ok = download_file(client.session, file_url, dest, fallback_url)
    if delay:
        CANCEL.wait(delay)  # like time.sleep(delay), but wakes up on Ctrl+C
    if ok:
        if dev_id:
            manifest.add(dev_id, dest.name)
        return "downloaded", f"Downloaded: {dest.name}"
    if CANCEL.is_set():
        return "cancelled", f"Cancelled: {title}"
    return "failed", f"FAILED: {dest.name}"


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
    delay: float, workers: int, redownload_missing: bool, unblur: bool,
) -> dict | None:
    """Download every new work of one user. Returns the counts per status,
    or None when the gallery is empty / the user does not exist.

    Exits with code 130 if the user interrupts with Ctrl+C.
    """
    print(f"User: {username}")
    print("Fetching gallery listing...")
    deviations = fetch_gallery(client, username)
    if not deviations:
        return None
    print(f"\nTotal works found: {len(deviations)}\n")

    out_dir = output_root / username
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = DownloadManifest(out_dir)

    # Save the full metadata in case it is needed later
    with open(out_dir / "_metadata.json", "w", encoding="utf-8") as f:
        json.dump(deviations, f, ensure_ascii=False, indent=2)

    counts = {"downloaded": 0, "skipped": 0, "failed": 0, "no_media": 0, "cancelled": 0}
    total = len(deviations)
    done = 0
    interrupted = False

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(process_deviation, client, dev, out_dir, delay, manifest,
                        redownload_missing, unblur): dev
            for dev in deviations
        }
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
            pool.shutdown(cancel_futures=True)

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


def run():
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Download the full gallery of a DeviantArt profile using the official API."
    )
    parser.add_argument(
        "profile_url",
        metavar="profile",
        nargs="?",
        help="Profile URL (https://www.deviantart.com/username) or just the "
             "username. If omitted, every user already downloaded to the "
             "output folder is synced with their latest works",
    )
    parser.add_argument("--login", action="store_true",
                        help="Log in with your DeviantArt account (OAuth) and save the "
                             "session. Mature works are then downloaded unblurred if "
                             "your account has mature content enabled")
    parser.add_argument("-o", "--output",
                        default=os.environ.get("DA_OUTPUT", "").strip() or "downloads",
                        help="Output folder, absolute or relative (default: DA_OUTPUT "
                             "from .env or 'downloads')")
    parser.add_argument("--client-id", default=os.environ.get("DA_CLIENT_ID"))
    parser.add_argument("--client-secret", default=os.environ.get("DA_CLIENT_SECRET"))
    parser.add_argument("--delay", type=float, default=0.5,
                        help="Pause in seconds after each download, per thread (default: 0.5)")
    parser.add_argument("-w", "--workers", type=int, default=env_int("DA_WORKERS", 4),
                        help="Simultaneous downloads (default: DA_WORKERS from .env or 4, "
                             "recommended not to exceed 8)")
    parser.add_argument("--unblur", action="store_true",
                        default=env_bool("DA_UNBLUR", False),
                        help="Strip the blur filter the API applies to mature-content "
                             "previews (default: keep the blur, or DA_UNBLUR from .env)")
    parser.add_argument("--redownload-missing", action="store_true",
                        help="Download again works recorded in the manifest whose local "
                             "file is missing (by default, manually deleted files are "
                             "not downloaded again)")
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

    client = DeviantArtClient(args.client_id, args.client_secret)

    if args.login:
        login(client)
        if not args.profile_url:
            return  # login-only invocation

    output_root = Path(args.output).expanduser()
    if args.profile_url:
        usernames = [extract_username(args.profile_url)]
    else:
        # No profile: sync every user already downloaded to the output folder
        usernames = discover_users(output_root)
        print(
            f"No profile given: syncing {len(usernames)} previously "
            f"downloaded user(s) in {output_root}: {', '.join(usernames)}\n"
        )

    if client.user_mode:
        print("Using the saved user session (mature works come unblurred if "
              "your account allows them).")

    totals = {"downloaded": 0, "skipped": 0, "failed": 0, "no_media": 0, "cancelled": 0}
    for username in usernames:
        counts = sync_gallery(
            client, username, output_root,
            delay=args.delay, workers=args.workers,
            redownload_missing=args.redownload_missing, unblur=args.unblur,
        )
        if counts is None:
            if args.profile_url:
                sys.exit("The gallery is empty or the user does not exist.")
            print(f"Skipping {username}: the gallery is empty or the user no longer exists.\n")
            continue
        for status, count in counts.items():
            totals[status] += count
        print()

    if len(usernames) > 1:
        print(
            f"All users synced. Downloaded: {totals['downloaded']} "
            f"| Skipped (already existed): {totals['skipped']} "
            f"| No file: {totals['no_media']} | Failed: {totals['failed']}"
        )


def main():
    try:
        run()
    except ApiError as e:
        sys.exit(f"\n{e}")
    except KeyboardInterrupt:
        # Ctrl+C outside the download loop (login, gallery listing, ...)
        print("\nInterrupted by the user.")
        sys.exit(130)


if __name__ == "__main__":
    main()
