"""The official OAuth2 API: the only route that can serve mature content."""

import json
import sys
import threading
import time
from pathlib import Path

import requests

from .constants import (API_BASE, API_RATE, CANCEL, TOKEN_FILE, TOKEN_URL,
                        USER_AGENT, sleep_or_cancel)

MAX_ATTEMPTS = 10
BASE_BACKOFF = 4      # seconds to hold off after the first 429
MAX_BACKOFF = 300     # and the ceiling the doubling stops at


class ApiError(RuntimeError):
    """The API kept failing after exhausting every retry."""


class UserNotFoundError(ApiError):
    """The profile does not exist or its owner deactivated their account.

    A deactivated or missing profile answers gallery/all with HTTP 400 rather
    than an empty listing, so it is singled out from other client errors.
    """


# Phrases the API puts in error_description when a profile cannot be listed
# because it is gone: no longer exists, or its owner deactivated the account
# (e.g. "Account is inactive.", "User \"x\" not found."). Any other 400 (a bad
# parameter, say) carries a different description and is left to raise normally.
_PROFILE_GONE_MARKERS = ("not found", "inactive", "deactivated", "deleted",
                         "disabled", "banned", "suspended")


def _user_not_found(resp: requests.Response) -> str | None:
    """The API's message when a 400 means the profile is gone, else None."""
    try:
        body = resp.json()
    except ValueError:
        return None
    description = str(body.get("error_description") or "")
    lowered = description.lower()
    if any(marker in lowered for marker in _PROFILE_GONE_MARKERS):
        return description
    return None


class RateLimiter:
    """Paces every API request, and holds the whole pool back after a 429.

    DeviantArt answers an overrun with "user_api_threshold": the limit is per
    account rather than per endpoint, and it reacts to short bursts more than
    to a running total, so the pacing is one rate shared by every call.

    The cool-down is shared for the same reason. A 429 means the account is
    going too fast, which is true of every worker at once, not just the one
    that happened to be told; letting the others keep firing only earns more
    429s and a longer block. So the first worker to see one backs the whole
    pool off, and the rest wait that out instead of each starting a ladder.
    """

    def __init__(self, rate: float = API_RATE):
        # A rate of 0 disables the pacing (the cool-down still applies).
        self.rate = rate
        self._lock = threading.Lock()
        # The two deadlines are kept apart: _next_slot is the pacing alone and
        # _blocked_until the cool-down alone, and acquire honours whichever is
        # further out. Folding one into the other would hide which is in force.
        self._next_slot = 0.0        # earliest monotonic time for the next request
        self._blocked_until = 0.0    # cool-down after a 429, shared by every thread
        self._backoff = 0.0          # current rung of the ladder, reset by a success

    def acquire(self) -> None:
        """Block until this thread may issue a request.

        Each caller reserves the next free slot under the lock, so concurrent
        workers queue up at the configured rate instead of racing each other.
        """
        interval = 1.0 / self.rate if self.rate > 0 else 0.0
        with self._lock:
            slot = max(self._next_slot, self._blocked_until, time.monotonic())
            self._next_slot = slot + interval
        # Consulted even when there is nothing to wait for, so a 'q' pressed
        # mid-run stops the pool before it issues another request.
        sleep_or_cancel(slot - time.monotonic())

    def penalise(self, retry_after: float | None = None) -> float:
        """Back the whole pool off after a 429; returns the seconds held.

        Doubling once per worker would turn one overrun into a several-minute
        block, so only the first worker to see a 429 climbs the ladder.
        """
        with self._lock:
            now = time.monotonic()
            # A cool-down already running means another worker has paid for this
            # round; leaving retry_after None then only reports what is left.
            if retry_after is None and now >= self._blocked_until:
                self._backoff = (min(self._backoff * 2, MAX_BACKOFF)
                                 if self._backoff else BASE_BACKOFF)
                retry_after = self._backoff
            self._blocked_until = max(self._blocked_until, now + (retry_after or 0.0))
            return self._blocked_until - now

    def succeeded(self) -> None:
        """A request got through: start the ladder from the bottom again."""
        with self._lock:
            self._backoff = 0.0


class DeviantArtClient:
    def __init__(self, client_id: str, client_secret: str,
                 token_file: Path = TOKEN_FILE, api_rate: float = API_RATE):
        self.client_id = client_id
        self.client_secret = client_secret
        self.token_file = token_file
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        # Shared by every worker: one client instance serves the whole run.
        self.limiter = RateLimiter(api_rate)
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
        # Token POSTs draw on the same per-account budget as everything else,
        # and a 401 loop can fire several in a row; pace them too.
        self.limiter.acquire()
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
        """GET against the API, paced, with token renewal and retries.

        Every request goes through the shared limiter, so the cool-down a 429
        sets is enforced by the same gate that paces ordinary calls: the wait
        happens on the next acquire rather than in this loop.
        """
        self._ensure_token()

        url = f"{API_BASE}/{endpoint.lstrip('/')}"
        for attempt in range(MAX_ATTEMPTS):
            self.limiter.acquire()
            resp = self.session.get(url, params=params, timeout=30)
            if resp.status_code == 401:
                self._ensure_token(force=True)
                continue
            if resp.status_code == 429:
                if attempt + 1 == MAX_ATTEMPTS:
                    break
                # DeviantArt does not currently send Retry-After; it is honoured
                # in case that changes, and the ladder covers its absence.
                header = resp.headers.get("Retry-After", "")
                held = self.limiter.penalise(int(header) if header.isdigit() else None)
                print(f"  Rate limit reached, holding every worker for {held:.0f} s...")
                continue
            if resp.status_code == 400 and (detail := _user_not_found(resp)):
                raise UserNotFoundError(detail)
            resp.raise_for_status()
            self.limiter.succeeded()
            return resp.json()
        advice = (f" Consider lowering DA_API_RATE (currently "
                  f"{self.limiter.rate:g}/s)." if self.limiter.rate else "")
        raise ApiError(
            f"DeviantArt kept rate-limiting {url} after every retry "
            f"(the block usually clears after a few minutes).\n"
            f"Try again later.{advice}"
        )
