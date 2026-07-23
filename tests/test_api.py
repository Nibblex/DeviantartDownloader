"""The OAuth2 client: tokens, retries and rate limiting."""

import json
import time

import pytest
import requests

from deviantart_downloader import api
from deviantart_downloader.constants import API_BASE, CANCEL

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

    def test_api_get_raises_user_not_found_on_deactivated_profile(self, tmp_path):
        session = FakeSession(get_responses=[FakeResponse(
            400, {"error": "invalid_request",
                  "error_description": 'User "ghost" not found.'})])
        client = make_client(tmp_path, session)
        with pytest.raises(api.UserNotFoundError, match="not found"):
            client.api_get("gallery/all", params={"username": "ghost"})

    def test_api_get_other_400_still_raises_http_error(self, tmp_path):
        session = FakeSession(get_responses=[FakeResponse(
            400, {"error": "invalid_request",
                  "error_description": "Invalid offset."})])
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
        with pytest.raises(RuntimeError, match="Cancelled"):
            client.api_get("gallery/all")
