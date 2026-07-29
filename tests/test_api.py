"""The OAuth2 client: tokens, retries and rate limiting."""

import json
import time

import pytest
import requests

from deviantart_downloader import api
from deviantart_downloader.constants import API_BASE, CANCEL, CancelledByUser

from .conftest import FakeResponse, FakeSession, make_client, token_response


class TestDeviantArtClient:
    def test_user_mode_reflects_token_file(self, tmp_path):
        client = make_client(tmp_path, FakeSession())
        assert client.user_mode is False
        client.token_file.write_text('{"refresh_token": "r"}', encoding="utf-8")
        assert client.user_mode is True

    def test_client_credentials_token_is_applied(self, tmp_path):
        session = FakeSession(post_responses=[token_response()])
        client = make_client(tmp_path, session, fresh_token=False)
        client._ensure_token()
        assert client.session.headers["Authorization"] == "Bearer tok"
        assert client._token_expiry > time.time()
        assert session.post_calls[0][1]["grant_type"] == "client_credentials"

    def test_token_request_failure_exits(self, tmp_path):
        session = FakeSession(post_responses=[FakeResponse(401, text="bad creds")])
        client = make_client(tmp_path, session, fresh_token=False)
        with pytest.raises(SystemExit, match="bad creds"):
            client._ensure_token()

    def test_user_mode_refresh_rotates_saved_token(self, tmp_path):
        session = FakeSession(post_responses=[token_response()])
        client = make_client(tmp_path, session, fresh_token=False)
        client.token_file.write_text('{"refresh_token": "old"}', encoding="utf-8")
        client._ensure_token()
        assert session.post_calls[0][1]["grant_type"] == "refresh_token"
        assert session.post_calls[0][1]["refresh_token"] == "old"
        saved = json.loads(client.token_file.read_text(encoding="utf-8"))
        assert saved == {"refresh_token": "ref"}

    def test_corrupt_saved_token_exits(self, tmp_path):
        client = make_client(tmp_path, FakeSession(), fresh_token=False)
        client.token_file.write_text("{not json", encoding="utf-8")
        with pytest.raises(SystemExit, match="--login"):
            client._ensure_token()

    def test_api_get_returns_json(self, tmp_path):
        session = FakeSession(get_responses=[FakeResponse(200, {"ok": True})])
        client = make_client(tmp_path, session)
        assert client.api_get("gallery/all", params={"a": 1}) == {"ok": True}
        url, kwargs = session.get_calls[0]
        assert url == f"{API_BASE}/gallery/all"
        assert kwargs["params"] == {"a": 1}

    def test_api_get_http_error_propagates(self, tmp_path):
        session = FakeSession(get_responses=[FakeResponse(500)])
        client = make_client(tmp_path, session)
        with pytest.raises(requests.HTTPError):
            client.api_get("gallery/all")

    @pytest.mark.parametrize("description", [
        'User "ghost" not found.',       # profile never existed
        "Account is inactive.",          # owner deactivated the account
    ])
    def test_api_get_raises_user_not_found_on_gone_profile(self, tmp_path,
                                                           description):
        session = FakeSession(get_responses=[FakeResponse(
            400, {"error": "invalid_request", "error_description": description})])
        client = make_client(tmp_path, session)
        with pytest.raises(api.UserNotFoundError, match="."):
            client.api_get("gallery/all", params={"username": "ghost"})

    def test_api_get_other_400_still_raises_http_error(self, tmp_path):
        session = FakeSession(get_responses=[FakeResponse(
            400, {"error": "invalid_request",
                  "error_description": "Request parameters are invalid."})])
        client = make_client(tmp_path, session)
        with pytest.raises(requests.HTTPError):
            client.api_get("gallery/all")

    def test_api_get_refreshes_token_on_401(self, tmp_path):
        session = FakeSession(
            get_responses=[FakeResponse(401), FakeResponse(200, {"ok": True})],
            post_responses=[token_response()],
        )
        client = make_client(tmp_path, session)
        assert client.api_get("gallery/all") == {"ok": True}
        assert client.session.headers["Authorization"] == "Bearer tok"

    def test_api_get_retries_on_429(self, tmp_path, capsys):
        session = FakeSession(get_responses=[
            FakeResponse(429, headers={"Retry-After": "0"}),
            FakeResponse(200, {"ok": True}),
        ])
        client = make_client(tmp_path, session)
        assert client.api_get("gallery/all") == {"ok": True}
        assert "Rate limit" in capsys.readouterr().out

    def test_api_get_gives_up_after_persistent_429(self, tmp_path):
        session = FakeSession(get_responses=[
            FakeResponse(429, headers={"Retry-After": "0"}) for _ in range(10)
        ])
        client = make_client(tmp_path, session)
        with pytest.raises(api.ApiError):
            client.api_get("gallery/all")

    def test_api_get_429_wait_aborts_on_cancel(self, tmp_path):
        session = FakeSession(get_responses=[
            FakeResponse(429, headers={"Retry-After": "0"}),
        ])
        client = make_client(tmp_path, session)
        CANCEL.set()
        with pytest.raises(CancelledByUser):
            client.api_get("gallery/all")


class TestRateLimiter:
    """The shared pacing and cool-down that keep the pool under the threshold."""

    def test_acquire_spaces_requests_out(self):
        limiter = api.RateLimiter(rate=100)      # 10 ms apart
        started = time.monotonic()
        for _ in range(3):
            limiter.acquire()
        # The first request goes straight through; only the gaps are paid for.
        assert time.monotonic() - started >= 0.02

    def test_a_rate_of_zero_disables_the_pacing(self):
        limiter = api.RateLimiter(rate=0)
        started = time.monotonic()
        for _ in range(50):
            limiter.acquire()
        assert time.monotonic() - started < 0.05

    def next_rung(self, limiter):
        """The next 429, as if the previous cool-down had already elapsed.

        Skipping ahead is the only way to climb the ladder without really
        sleeping for it, and nothing public exposes the deadline.
        """
        limiter._blocked_until = 0
        return limiter.penalise()

    def test_penalise_climbs_the_ladder(self):
        limiter = api.RateLimiter(rate=0)
        rungs = [self.next_rung(limiter) for _ in range(3)]
        assert rungs == pytest.approx(
            [api.BASE_BACKOFF, api.BASE_BACKOFF * 2, api.BASE_BACKOFF * 4], abs=0.1)

    def test_the_ladder_stops_at_the_ceiling(self):
        limiter = api.RateLimiter(rate=0)
        for _ in range(20):
            held = self.next_rung(limiter)
        assert held == pytest.approx(api.MAX_BACKOFF, abs=0.1)

    def test_a_second_worker_waits_out_the_cool_down_instead_of_doubling(self):
        limiter = api.RateLimiter(rate=0)
        first = limiter.penalise()
        # Three more workers hit the same 429 before the first wait elapsed.
        others = [limiter.penalise() for _ in range(3)]
        assert all(held <= first for held in others)
        # One overrun costs one rung, however many workers noticed it.
        assert limiter._backoff == api.BASE_BACKOFF

    def test_an_explicit_retry_after_wins_over_the_ladder(self):
        limiter = api.RateLimiter(rate=0)
        assert limiter.penalise(retry_after=42) == pytest.approx(42, abs=0.1)
        assert limiter._backoff == 0          # the ladder was never climbed

    def test_a_success_resets_the_ladder(self):
        limiter = api.RateLimiter(rate=0)
        self.next_rung(limiter)
        self.next_rung(limiter)               # climbed to the second rung
        limiter.succeeded()
        assert self.next_rung(limiter) == pytest.approx(api.BASE_BACKOFF, abs=0.1)

    def test_the_cool_down_holds_every_thread(self):
        limiter = api.RateLimiter(rate=0)
        limiter.penalise(retry_after=0.05)
        started = time.monotonic()
        limiter.acquire()                     # a thread that never saw the 429
        assert time.monotonic() - started >= 0.04

    def test_acquire_aborts_when_the_user_quits(self):
        limiter = api.RateLimiter(rate=0)
        CANCEL.set()
        with pytest.raises(CancelledByUser):
            limiter.acquire()


class TestApiGetPacing:
    def test_a_429_holds_the_pool_and_a_success_clears_it(self, tmp_path, capsys,
                                                          monkeypatch):
        # A real, but negligible, rung: the cool-down has to be genuinely taken
        # for the reset afterwards to mean anything.
        monkeypatch.setattr(api, "BASE_BACKOFF", 0.01)
        session = FakeSession(get_responses=[
            FakeResponse(429),                # no Retry-After, as DeviantArt sends
            FakeResponse(200, {"ok": True}),
        ])
        client = make_client(tmp_path, session)
        assert client.api_get("gallery/all") == {"ok": True}
        assert "Rate limit reached" in capsys.readouterr().out
        assert client.limiter._backoff == 0        # reset by the success

    def test_every_request_goes_through_the_limiter(self, tmp_path):
        session = FakeSession(get_responses=[FakeResponse(200, {"ok": True})])
        client = make_client(tmp_path, session)
        seen = []
        client.limiter.acquire = lambda: seen.append(1)
        client.api_get("gallery/all")
        assert seen == [1]


class TestUnreadableProfiles:
    """Gone, deactivated and blocked all mean the same to a batch: move on."""

    @pytest.mark.parametrize("description", [
        'User "ghost" not found.',                          # never existed
        "Account is inactive.",                             # owner deactivated it
        "Sorry, we have blocked access to this profile.",   # closed to us
    ])
    def test_an_unreadable_profile_is_singled_out(self, tmp_path, description):
        session = FakeSession(get_responses=[FakeResponse(
            400, {"error": "invalid_request", "error_description": description})])
        with pytest.raises(api.UserNotFoundError, match="."):
            make_client(tmp_path, session).api_get("user/profile/x")

    def test_an_unrelated_400_still_raises_plainly(self, tmp_path):
        session = FakeSession(get_responses=[FakeResponse(
            400, {"error": "invalid_request",
                  "error_description": "Request parameters are invalid."})])
        with pytest.raises(requests.HTTPError):
            make_client(tmp_path, session).api_get("user/profile/x")

    def test_a_batch_treats_a_failed_request_as_that_user_ending(self):
        # Both are about the profile that was asked for...
        assert issubclass(api.UserNotFoundError, api.UNREADABLE_PROFILE)
        assert issubclass(requests.HTTPError, api.UNREADABLE_PROFILE)
        # ...while giving up after every retry is about the account as a whole,
        # and would only repeat itself on the next user.
        assert not issubclass(api.ApiError, api.UNREADABLE_PROFILE)
