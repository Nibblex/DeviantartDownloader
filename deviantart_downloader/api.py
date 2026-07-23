"""The official OAuth2 API: the only route that can serve mature content."""

import json
import sys
import threading
import time
from pathlib import Path

import requests

from .constants import API_BASE, CANCEL, TOKEN_FILE, TOKEN_URL, USER_AGENT


class ApiError(RuntimeError):
    """The API kept failing after exhausting every retry."""


class UserNotFoundError(ApiError):
    """The profile does not exist or its owner deactivated their account.

    A deactivated or missing profile answers gallery/all with HTTP 400 rather
    than an empty listing, so it is singled out from other client errors.
    """


def _user_not_found(resp: requests.Response) -> str | None:
    """The API's message when a 400 means the user is gone, else None.

    A deactivated or non-existent profile answers with a body like
    {"error_description": "User \\"x\\" not found."}; any other 400 (a bad
    parameter, say) carries a different description and is left to raise.
    """
    try:
        body = resp.json()
    except ValueError:
        return None
    description = str(body.get("error_description") or "")
    return description if "not found" in description.lower() else None


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
            if resp.status_code == 400 and (detail := _user_not_found(resp)):
                raise UserNotFoundError(detail)
            resp.raise_for_status()
            return resp.json()
        raise ApiError(
            f"DeviantArt kept rate-limiting {url} after every retry "
            "(the block usually clears after a few minutes).\n"
            "Try again later, and consider lowering DA_WORKERS to 4 or less "
            "if it keeps happening."
        )
