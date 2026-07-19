"""Test suite for deviantart_downloader.

Everything runs offline: HTTP traffic is simulated with fake sessions and
responses, and the interactive login flow is not exercised.
"""

import json
import sys
import threading
import time

import pytest
import requests

import deviantart_downloader as dd


@pytest.fixture(autouse=True)
def fresh_cancel(monkeypatch):
    """Give every test its own CANCEL event so tests cannot leak state."""
    monkeypatch.setattr(dd, "CANCEL", threading.Event())


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, headers=None,
                 chunks=None, text=""):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.headers = headers or {}
        self._chunks = chunks if chunks is not None else [b"data"]
        self.text = text

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size):
        yield from self._chunks

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeSession:
    """Scripted requests.Session: queues of responses for get() and post()."""

    def __init__(self, get_responses=(), post_responses=()):
        self.headers = {}
        self.get_responses = list(get_responses)
        self.post_responses = list(post_responses)
        self.get_calls = []
        self.post_calls = []

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return self.get_responses.pop(0)

    def post(self, url, data=None, **kwargs):
        self.post_calls.append((url, data))
        return self.post_responses.pop(0)


def token_response():
    return FakeResponse(200, {"access_token": "tok", "refresh_token": "ref",
                              "expires_in": 3600})


def make_client(tmp_path, session, fresh_token=True):
    client = dd.DeviantArtClient("id", "secret", token_file=tmp_path / "token.json")
    client.session = session
    if fresh_token:
        client._token_expiry = time.time() + 1000
    return client


# ---------------------------------------------------------------------------
# Environment helpers
# ---------------------------------------------------------------------------

class TestLoadDotenv:
    def test_parses_values_and_ignores_noise(self, tmp_path, monkeypatch):
        env = tmp_path / ".env"
        env.write_text(
            "# a comment\n"
            "\n"
            "TESTDD_PLAIN=hello\n"
            "TESTDD_QUOTED='quoted value'\n"
            "TESTDD_SPACED =  padded  \n"
            "not-a-valid-line\n",
            encoding="utf-8",
        )
        monkeypatch.delenv("TESTDD_PLAIN", raising=False)
        dd.load_dotenv(env)
        try:
            assert dd.os.environ["TESTDD_PLAIN"] == "hello"
            assert dd.os.environ["TESTDD_QUOTED"] == "quoted value"
            assert dd.os.environ["TESTDD_SPACED"] == "padded"
        finally:
            for key in ("TESTDD_PLAIN", "TESTDD_QUOTED", "TESTDD_SPACED"):
                dd.os.environ.pop(key, None)

    def test_does_not_overwrite_existing_variables(self, tmp_path, monkeypatch):
        env = tmp_path / ".env"
        env.write_text("TESTDD_KEEP=from_file\n", encoding="utf-8")
        monkeypatch.setenv("TESTDD_KEEP", "original")
        dd.load_dotenv(env)
        assert dd.os.environ["TESTDD_KEEP"] == "original"

    def test_missing_file_is_a_no_op(self, tmp_path):
        dd.load_dotenv(tmp_path / "does-not-exist")

    def test_discovers_env_in_cwd(self, tmp_path, monkeypatch):
        (tmp_path / ".env").write_text("TESTDD_CWD=yes\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("TESTDD_CWD", raising=False)
        dd.load_dotenv()
        try:
            assert dd.os.environ["TESTDD_CWD"] == "yes"
        finally:
            dd.os.environ.pop("TESTDD_CWD", None)


class TestEnvInt:
    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("TESTDD_INT", raising=False)
        assert dd.env_int("TESTDD_INT", 7) == 7

    def test_reads_integer(self, monkeypatch):
        monkeypatch.setenv("TESTDD_INT", " 12 ")
        assert dd.env_int("TESTDD_INT", 7) == 12

    def test_invalid_value_exits(self, monkeypatch):
        monkeypatch.setenv("TESTDD_INT", "banana")
        with pytest.raises(SystemExit):
            dd.env_int("TESTDD_INT", 7)


class TestEnvBool:
    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("TESTDD_BOOL", raising=False)
        assert dd.env_bool("TESTDD_BOOL", True) is True
        assert dd.env_bool("TESTDD_BOOL", False) is False

    @pytest.mark.parametrize("value", ["1", "true", "YES", "On"])
    def test_truthy_values(self, monkeypatch, value):
        monkeypatch.setenv("TESTDD_BOOL", value)
        assert dd.env_bool("TESTDD_BOOL", False) is True

    @pytest.mark.parametrize("value", ["0", "false", "NO", "Off"])
    def test_falsy_values(self, monkeypatch, value):
        monkeypatch.setenv("TESTDD_BOOL", value)
        assert dd.env_bool("TESTDD_BOOL", True) is False

    def test_invalid_value_exits(self, monkeypatch):
        monkeypatch.setenv("TESTDD_BOOL", "maybe")
        with pytest.raises(SystemExit):
            dd.env_bool("TESTDD_BOOL", False)


# ---------------------------------------------------------------------------
# URL / name helpers
# ---------------------------------------------------------------------------

class TestExtractUsername:
    @pytest.mark.parametrize("url,expected", [
        ("https://www.deviantart.com/someartist", "someartist"),
        ("https://www.deviantart.com/someartist/gallery/all", "someartist"),
        ("www.deviantart.com/someartist", "someartist"),
        ("https://someartist.deviantart.com", "someartist"),
        ("someartist", "someartist"),
    ])
    def test_valid_inputs(self, url, expected):
        assert dd.extract_username(url) == expected

    @pytest.mark.parametrize("url", [
        "https://www.deviantart.com",   # no username in the path
        "some.user",                    # dots are not allowed in bare names
        "https://example.com/whoever",  # not a DeviantArt URL
    ])
    def test_invalid_inputs_exit(self, url):
        with pytest.raises(SystemExit):
            dd.extract_username(url)


class TestSanitizeFilename:
    def test_replaces_forbidden_characters(self):
        assert dd.sanitize_filename('a<b>:c"d/e\\f|g?h*i') == "a_b__c_d_e_f_g_h_i"

    def test_strips_control_characters(self):
        assert dd.sanitize_filename("a\x00b\x1fc") == "a_b_c"

    def test_strips_leading_trailing_dots_and_spaces(self):
        assert dd.sanitize_filename("  .name.  ") == "name"

    def test_truncates_long_names(self):
        assert len(dd.sanitize_filename("x" * 300)) == 150

    @pytest.mark.parametrize("name", ["", "  ", "..."])
    def test_empty_becomes_untitled(self, name):
        assert dd.sanitize_filename(name) == "untitled"


class TestUnblurWixmpUrl:
    def test_strips_blur_from_wixmp_urls(self):
        url = "https://images-wixmp-abc.wixmp.com/f/x/y.png/v1/fill/w_1,h_1,q_80,blur_16/pic.png?token=t"
        assert ",blur_16" not in dd.unblur_wixmp_url(url)

    def test_only_first_blur_segment_is_removed(self):
        url = "https://images-wixmp-abc.wixmp.com/a,blur_16/b,blur_16/pic.png"
        assert dd.unblur_wixmp_url(url).count(",blur_16") == 1

    def test_non_wixmp_urls_are_untouched(self):
        url = "https://example.com/a,blur_16/pic.png"
        assert dd.unblur_wixmp_url(url) == url


class TestGuessExtension:
    @pytest.mark.parametrize("url,expected", [
        ("https://example.com/dir/pic.png", ".png"),
        ("https://example.com/dir/pic.JPEG?token=abc", ".jpeg"),
        ("https://example.com/dir/pic%20name.gif", ".gif"),
        ("https://example.com/dir/noext", ".jpg"),
        ("https://example.com/dir/weird.superlong", ".jpg"),
    ])
    def test_extensions(self, url, expected):
        assert dd.guess_extension(url) == expected


# ---------------------------------------------------------------------------
# DownloadManifest
# ---------------------------------------------------------------------------

DEV_ID = "abcd1234-5678-90ab-cdef-1234567890ab"


class TestDownloadManifest:
    def test_add_has_and_filename_for(self, tmp_path):
        manifest = dd.DownloadManifest(tmp_path)
        assert not manifest.has(DEV_ID)
        manifest.add(DEV_ID, "art.png")
        assert manifest.has(DEV_ID)
        assert manifest.filename_for(DEV_ID) == "art.png"
        # The key is the first 8 chars, case-insensitive
        assert manifest.has(DEV_ID.upper())

    def test_persists_across_instances(self, tmp_path):
        dd.DownloadManifest(tmp_path).add(DEV_ID, "art.png")
        reloaded = dd.DownloadManifest(tmp_path)
        assert reloaded.filename_for(DEV_ID) == "art.png"
        data = json.loads((tmp_path / "_downloaded.json").read_text(encoding="utf-8"))
        assert data == {"ABCD1234": "art.png"}

    def test_corrupt_manifest_warns_and_starts_empty(self, tmp_path, capsys):
        (tmp_path / "_downloaded.json").write_text("{not json", encoding="utf-8")
        manifest = dd.DownloadManifest(tmp_path)
        assert "WARNING" in capsys.readouterr().out
        assert not manifest.has(DEV_ID)

    def test_seeds_from_existing_files(self, tmp_path):
        (tmp_path / "Some Art_ABCD1234.png").write_bytes(b"x")
        (tmp_path / "lowercase_ffff0000.jpg").write_bytes(b"x")
        (tmp_path / "no-id-suffix.png").write_bytes(b"x")
        (tmp_path / "_metadata.json").write_text("[]", encoding="utf-8")
        (tmp_path / "partial_12345678.png.part").write_bytes(b"x")
        manifest = dd.DownloadManifest(tmp_path)
        assert manifest.filename_for(DEV_ID) == "Some Art_ABCD1234.png"
        assert manifest.has("ffff0000-aaaa")
        assert not manifest.has("12345678")


# ---------------------------------------------------------------------------
# DeviantArtClient
# ---------------------------------------------------------------------------

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
        assert url == f"{dd.API_BASE}/gallery/all"
        assert kwargs["params"] == {"a": 1}

    def test_api_get_http_error_propagates(self, tmp_path):
        session = FakeSession(get_responses=[FakeResponse(500)])
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
        with pytest.raises(dd.ApiError):
            client.api_get("gallery/all")

    def test_api_get_429_wait_aborts_on_cancel(self, tmp_path):
        session = FakeSession(get_responses=[
            FakeResponse(429, headers={"Retry-After": "0"}),
        ])
        client = make_client(tmp_path, session)
        dd.CANCEL.set()
        with pytest.raises(RuntimeError, match="Cancelled"):
            client.api_get("gallery/all")


# ---------------------------------------------------------------------------
# fetch_gallery
# ---------------------------------------------------------------------------

class FakeClient:
    def __init__(self, pages=None):
        self.pages = list(pages or [])
        self.calls = []
        self.session = FakeSession()

    def api_get(self, endpoint, params=None):
        self.calls.append((endpoint, params))
        return self.pages.pop(0)


def test_fetch_gallery_walks_every_page(capsys):
    client = FakeClient(pages=[
        {"results": [{"deviationid": "1"}], "has_more": True, "next_offset": 24},
        {"results": [{"deviationid": "2"}], "has_more": False},
    ])
    deviations = dd.fetch_gallery(client, "artist")
    assert [d["deviationid"] for d in deviations] == ["1", "2"]
    assert client.calls[0][1]["offset"] == 0
    assert client.calls[1][1]["offset"] == 24
    assert client.calls[0][1]["username"] == "artist"


class TestFetchGalleryEarlyStop:
    def make_pages(self):
        return [
            {"results": [make_dev()], "has_more": True, "next_offset": 24},
            {"results": [make_dev(deviationid="ffffeeee-0000")], "has_more": False},
        ]

    def test_stops_at_fully_downloaded_page(self, tmp_path, capsys):
        manifest = dd.DownloadManifest(tmp_path)
        manifest.add(DEV_ID, "My Art_abcd1234.png")
        client = FakeClient(pages=self.make_pages())
        deviations = dd.fetch_gallery(client, "artist", manifest=manifest)
        assert [d["deviationid"] for d in deviations] == [DEV_ID]
        assert len(client.calls) == 1
        assert "stopping the listing early" in capsys.readouterr().out

    def test_full_walks_past_downloaded_pages(self, tmp_path):
        manifest = dd.DownloadManifest(tmp_path)
        manifest.add(DEV_ID, "My Art_abcd1234.png")
        client = FakeClient(pages=self.make_pages())
        deviations = dd.fetch_gallery(client, "artist", manifest=manifest,
                                      full=True)
        assert len(deviations) == 2

    def test_no_manifest_walks_every_page(self, capsys):
        client = FakeClient(pages=self.make_pages())
        deviations = dd.fetch_gallery(client, "artist")
        assert len(deviations) == 2

    def test_keeps_walking_while_a_work_is_unrecorded(self, tmp_path):
        # A failed download is never recorded in the manifest, so its page
        # keeps the walk going until the work is retried successfully.
        manifest = dd.DownloadManifest(tmp_path)
        manifest.add(DEV_ID, "My Art_abcd1234.png")
        client = FakeClient(pages=[
            {"results": [make_dev(), make_dev(deviationid="99999999-0000")],
             "has_more": True, "next_offset": 24},
            {"results": [make_dev(deviationid="ffffeeee-0000")], "has_more": False},
        ])
        deviations = dd.fetch_gallery(client, "artist", manifest=manifest)
        assert len(deviations) == 3

    def test_page_without_ids_does_not_stop(self, tmp_path):
        manifest = dd.DownloadManifest(tmp_path)
        client = FakeClient(pages=[
            {"results": [{"title": "no id"}], "has_more": True, "next_offset": 24},
            {"results": [make_dev(deviationid="ffffeeee-0000")], "has_more": False},
        ])
        deviations = dd.fetch_gallery(client, "artist", manifest=manifest)
        assert len(deviations) == 2


# ---------------------------------------------------------------------------
# download_file
# ---------------------------------------------------------------------------

class TestDownloadFile:
    def test_success_writes_file(self, tmp_path):
        session = FakeSession(get_responses=[
            FakeResponse(200, chunks=[b"abc", b"def"]),
        ])
        dest = tmp_path / "pic.png"
        assert dd.download_file(session, "https://x/pic.png", dest) is True
        assert dest.read_bytes() == b"abcdef"
        assert not list(tmp_path.glob("*.part"))

    def test_403_falls_back_to_blurred_url(self, tmp_path, capsys):
        session = FakeSession(get_responses=[
            FakeResponse(403),
            FakeResponse(200, chunks=[b"blurred"]),
        ])
        dest = tmp_path / "pic.png"
        ok = dd.download_file(session, "https://x/unblurred.png", dest,
                              fallback_url="https://x/blurred.png")
        assert ok is True
        assert dest.read_bytes() == b"blurred"
        assert session.get_calls[1][0] == "https://x/blurred.png"
        assert "Unblur rejected" in capsys.readouterr().out

    def test_http_error_returns_false_and_cleans_up(self, tmp_path, capsys):
        session = FakeSession(get_responses=[FakeResponse(404)])
        dest = tmp_path / "pic.png"
        assert dd.download_file(session, "https://x/pic.png", dest) is False
        assert not dest.exists()
        assert not list(tmp_path.glob("*.part"))
        assert "ERROR" in capsys.readouterr().out

    def test_cancel_aborts_mid_download(self, tmp_path):
        session = FakeSession(get_responses=[
            FakeResponse(200, chunks=[b"abc", b"def"]),
        ])
        dest = tmp_path / "pic.png"
        dd.CANCEL.set()
        assert dd.download_file(session, "https://x/pic.png", dest) is False
        assert not dest.exists()
        assert not list(tmp_path.glob("*.part"))


# ---------------------------------------------------------------------------
# process_deviation
# ---------------------------------------------------------------------------

@pytest.fixture
def manifest(tmp_path):
    return dd.DownloadManifest(tmp_path)


def make_dev(**overrides):
    dev = {
        "deviationid": DEV_ID,
        "title": "My Art",
        "content": {"src": "https://example.com/pic.png"},
    }
    dev.update(overrides)
    return dev


class TestProcessDeviation:
    def test_downloads_content_src(self, tmp_path, manifest, monkeypatch):
        downloads = []

        def fake_download(session, url, dest, fallback=None):
            downloads.append((url, fallback))
            dest.write_bytes(b"x")
            return True

        monkeypatch.setattr(dd, "download_file", fake_download)
        status, msg = dd.process_deviation(
            FakeClient(), make_dev(), tmp_path, 0, manifest)
        assert status == "downloaded"
        assert downloads == [("https://example.com/pic.png", None)]
        assert manifest.filename_for(DEV_ID) == "My Art_abcd1234.png"
        assert (tmp_path / "My Art_abcd1234.png").is_file()

    def test_prefers_original_download_url(self, tmp_path, manifest, monkeypatch):
        client = FakeClient(pages=[{"src": "https://example.com/original.png"}])
        downloads = []

        def fake_download(session, url, dest, fallback=None):
            downloads.append(url)
            dest.write_bytes(b"x")
            return True

        monkeypatch.setattr(dd, "download_file", fake_download)
        status, _ = dd.process_deviation(
            client, make_dev(is_downloadable=True), tmp_path, 0, manifest)
        assert status == "downloaded"
        assert downloads == ["https://example.com/original.png"]
        assert client.calls[0][0] == f"deviation/download/{DEV_ID}"

    def test_falls_back_when_download_endpoint_fails(self, tmp_path, manifest,
                                                     monkeypatch):
        class FailingClient(FakeClient):
            def api_get(self, endpoint, params=None):
                raise requests.HTTPError("boom")

        downloads = []

        def fake_download(session, url, dest, fallback=None):
            downloads.append(url)
            dest.write_bytes(b"x")
            return True

        monkeypatch.setattr(dd, "download_file", fake_download)
        status, _ = dd.process_deviation(
            FailingClient(), make_dev(is_downloadable=True), tmp_path, 0, manifest)
        assert status == "downloaded"
        assert downloads == ["https://example.com/pic.png"]

    def test_unblur_passes_blurred_url_as_fallback(self, tmp_path, manifest,
                                                   monkeypatch):
        blurred = "https://images-wixmp-abc.wixmp.com/f/pic.png/v1/fill/w_1,blur_16/pic.png"
        downloads = []

        def fake_download(session, url, dest, fallback=None):
            downloads.append((url, fallback))
            dest.write_bytes(b"x")
            return True

        monkeypatch.setattr(dd, "download_file", fake_download)
        status, _ = dd.process_deviation(
            FakeClient(), make_dev(content={"src": blurred}), tmp_path, 0,
            manifest, unblur=True)
        assert status == "downloaded"
        url, fallback = downloads[0]
        assert ",blur_16" not in url
        assert fallback == blurred

    def test_skips_already_downloaded(self, tmp_path, manifest):
        manifest.add(DEV_ID, "old name.png")
        (tmp_path / "old name.png").write_bytes(b"x")
        status, msg = dd.process_deviation(
            FakeClient(), make_dev(), tmp_path, 0, manifest)
        assert status == "skipped"
        assert "old name.png" in msg

    def test_skips_locally_deleted_by_default(self, tmp_path, manifest):
        manifest.add(DEV_ID, "deleted.png")
        status, msg = dd.process_deviation(
            FakeClient(), make_dev(), tmp_path, 0, manifest)
        assert status == "skipped"
        assert "Deleted locally" in msg

    def test_redownload_missing_restores_deleted(self, tmp_path, manifest,
                                                 monkeypatch):
        manifest.add(DEV_ID, "deleted.png")

        def fake_download(session, url, dest, fallback=None):
            dest.write_bytes(b"x")
            return True

        monkeypatch.setattr(dd, "download_file", fake_download)
        status, _ = dd.process_deviation(
            FakeClient(), make_dev(), tmp_path, 0, manifest,
            redownload_missing=True)
        assert status == "downloaded"

    def test_existing_file_with_same_name_is_recorded(self, tmp_path, manifest):
        (tmp_path / "My Art_abcd1234.png").write_bytes(b"x")
        status, _ = dd.process_deviation(
            FakeClient(), make_dev(), tmp_path, 0, manifest)
        assert status == "skipped"
        assert manifest.has(DEV_ID)

    def test_no_media_deviation(self, tmp_path, manifest):
        status, msg = dd.process_deviation(
            FakeClient(), make_dev(content=None), tmp_path, 0, manifest)
        assert status == "no_media"
        assert "NO FILE" in msg

    def test_failed_download(self, tmp_path, manifest, monkeypatch):
        monkeypatch.setattr(dd, "download_file",
                            lambda session, url, dest, fallback=None: False)
        status, _ = dd.process_deviation(
            FakeClient(), make_dev(), tmp_path, 0, manifest)
        assert status == "failed"
        assert not manifest.has(DEV_ID)

    def test_cancelled_before_start(self, tmp_path, manifest):
        dd.CANCEL.set()
        status, _ = dd.process_deviation(
            FakeClient(), make_dev(), tmp_path, 0, manifest)
        assert status == "cancelled"


# ---------------------------------------------------------------------------
# CLI (run / main)
# ---------------------------------------------------------------------------

@pytest.fixture
def clean_cli_env(tmp_path, monkeypatch):
    """Isolated cwd, no .env pickup (the repo may have a real one) and no
    DA_* variables."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(dd, "load_dotenv", lambda path=None: None)
    for var in ("DA_CLIENT_ID", "DA_CLIENT_SECRET", "DA_WORKERS", "DA_UNBLUR",
                "DA_OUTPUT"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def set_argv(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["deviantart-downloader", *args])


class TestRun:
    def test_requires_credentials(self, clean_cli_env, monkeypatch):
        set_argv(monkeypatch, "someartist")
        with pytest.raises(SystemExit, match="Missing API credentials"):
            dd.run()

    def test_no_profile_and_no_output_dir_exits(self, clean_cli_env, monkeypatch):
        set_argv(monkeypatch, "--client-id", "x", "--client-secret", "y")
        with pytest.raises(SystemExit, match="does not exist"):
            dd.run()

    def test_rejects_zero_workers(self, clean_cli_env, monkeypatch):
        set_argv(monkeypatch, "someartist", "--client-id", "x",
                 "--client-secret", "y", "-w", "0")
        with pytest.raises(SystemExit, match="at least 1"):
            dd.run()

    def test_empty_gallery_exits(self, clean_cli_env, monkeypatch):
        monkeypatch.setattr(dd, "fetch_gallery", lambda client, username, **kw: [])
        set_argv(monkeypatch, "someartist", "--client-id", "x",
                 "--client-secret", "y")
        with pytest.raises(SystemExit, match="empty"):
            dd.run()

    def test_end_to_end_download(self, clean_cli_env, monkeypatch, capsys):
        devs = [
            make_dev(),
            make_dev(deviationid="ffffeeee-0000", title="Journal", content=None),
        ]
        monkeypatch.setattr(dd, "fetch_gallery", lambda client, username, **kw: devs)

        def fake_download(session, url, dest, fallback=None):
            dest.write_bytes(b"x")
            return True

        monkeypatch.setattr(dd, "download_file", fake_download)
        out = clean_cli_env / "out"
        set_argv(monkeypatch, "https://www.deviantart.com/someartist",
                 "-o", str(out), "--client-id", "x", "--client-secret", "y",
                 "--delay", "0", "-w", "2")
        dd.run()

        gallery = out / "someartist"
        assert (gallery / "My Art_abcd1234.png").is_file()
        assert json.loads((gallery / "_metadata.json").read_text(encoding="utf-8")) == devs
        assert json.loads((gallery / "_downloaded.json").read_text(encoding="utf-8")) == {
            "ABCD1234": "My Art_abcd1234.png"
        }
        stdout = capsys.readouterr().out
        assert "Downloaded: 1" in stdout
        assert "No file: 1" in stdout

    def test_metadata_merges_across_runs(self, clean_cli_env, monkeypatch, capsys):
        fetch_kwargs = []
        batches = [
            [make_dev()],
            # Second run: the early stop only returned the newest work
            [make_dev(deviationid="ffffeeee-0000", title="New Art")],
        ]

        def fake_fetch(client, username, **kw):
            fetch_kwargs.append(kw)
            return batches.pop(0)

        def fake_download(session, url, dest, fallback=None):
            dest.write_bytes(b"x")
            return True

        monkeypatch.setattr(dd, "fetch_gallery", fake_fetch)
        monkeypatch.setattr(dd, "download_file", fake_download)
        out = clean_cli_env / "out"
        argv = ("someartist", "-o", str(out), "--client-id", "x",
                "--client-secret", "y", "--delay", "0")
        set_argv(monkeypatch, *argv)
        dd.run()
        set_argv(monkeypatch, *argv)
        dd.run()

        # No manifest before the first run; the second run can stop early
        assert fetch_kwargs[0]["manifest"] is None
        assert fetch_kwargs[1]["manifest"] is not None
        meta = json.loads(
            (out / "someartist" / "_metadata.json").read_text(encoding="utf-8"))
        assert [d["deviationid"] for d in meta] == ["ffffeeee-0000", DEV_ID]

    @pytest.mark.parametrize("flag", ["--full", "--redownload-missing"])
    def test_flags_force_the_full_listing(self, clean_cli_env, monkeypatch, flag):
        seen = {}

        def fake_fetch(client, username, **kw):
            seen.update(kw)
            return [make_dev()]

        def fake_download(session, url, dest, fallback=None):
            dest.write_bytes(b"x")
            return True

        monkeypatch.setattr(dd, "fetch_gallery", fake_fetch)
        monkeypatch.setattr(dd, "download_file", fake_download)
        out = clean_cli_env / "out"
        make_user_dir(out, "someartist")
        set_argv(monkeypatch, "someartist", "-o", str(out), "--client-id", "x",
                 "--client-secret", "y", "--delay", "0", flag)
        dd.run()
        assert seen["full"] is True


def make_user_dir(root, username, marker="_downloaded.json", content="{}"):
    d = root / username
    d.mkdir(parents=True)
    (d / marker).write_text(content, encoding="utf-8")
    return d


class TestDiscoverUsers:
    def test_finds_downloaded_users_sorted(self, tmp_path):
        make_user_dir(tmp_path, "zeta")
        make_user_dir(tmp_path, "alpha", marker="_metadata.json", content="[]")
        assert dd.discover_users(tmp_path) == ["alpha", "zeta"]

    def test_ignores_unrelated_entries(self, tmp_path):
        make_user_dir(tmp_path, "artist")
        (tmp_path / "random-folder").mkdir()          # no marker files
        (tmp_path / ".hidden").mkdir()
        (tmp_path / "_underscore").mkdir()
        (tmp_path / "loose-file.txt").write_bytes(b"x")
        assert dd.discover_users(tmp_path) == ["artist"]

    def test_missing_output_dir_exits(self, tmp_path):
        with pytest.raises(SystemExit, match="does not exist"):
            dd.discover_users(tmp_path / "nope")

    def test_no_users_exits(self, tmp_path):
        (tmp_path / "random-folder").mkdir()
        with pytest.raises(SystemExit, match="No previously downloaded users"):
            dd.discover_users(tmp_path)


class TestSyncAll:
    @pytest.fixture
    def galleries(self, monkeypatch):
        """Patch fetch_gallery/download_file; galleries dict drives the data."""
        galleries = {}
        monkeypatch.setattr(
            dd, "fetch_gallery",
            lambda client, username, **kw: galleries.get(username, []))

        def fake_download(session, url, dest, fallback=None):
            dest.write_bytes(b"x")
            return True

        monkeypatch.setattr(dd, "download_file", fake_download)
        return galleries

    def test_syncs_every_downloaded_user(self, clean_cli_env, monkeypatch,
                                         galleries, capsys):
        out = clean_cli_env / "out"
        make_user_dir(out, "alice")
        make_user_dir(out, "bob")
        galleries["alice"] = [make_dev()]
        galleries["bob"] = [make_dev(deviationid="ffffeeee-0000", title="Bob Art")]

        set_argv(monkeypatch, "-o", str(out), "--client-id", "x",
                 "--client-secret", "y", "--delay", "0")
        dd.run()

        assert (out / "alice" / "My Art_abcd1234.png").is_file()
        assert (out / "bob" / "Bob Art_ffffeeee.png").is_file()
        stdout = capsys.readouterr().out
        assert "syncing 2 previously downloaded user(s)" in stdout
        assert "All users synced. Downloaded: 2" in stdout

    def test_empty_gallery_is_skipped_not_fatal(self, clean_cli_env, monkeypatch,
                                                galleries, capsys):
        out = clean_cli_env / "out"
        make_user_dir(out, "ghost")     # deactivated account: empty gallery
        make_user_dir(out, "alice")
        galleries["alice"] = [make_dev()]

        set_argv(monkeypatch, "-o", str(out), "--client-id", "x",
                 "--client-secret", "y", "--delay", "0")
        dd.run()

        assert (out / "alice" / "My Art_abcd1234.png").is_file()
        stdout = capsys.readouterr().out
        assert "Skipping ghost" in stdout

    def test_explicit_profile_with_empty_gallery_still_exits(
            self, clean_cli_env, monkeypatch, galleries):
        set_argv(monkeypatch, "someartist", "-o", str(clean_cli_env / "out"),
                 "--client-id", "x", "--client-secret", "y")
        with pytest.raises(SystemExit, match="empty"):
            dd.run()

    def test_sync_reuses_manifest_and_skips_existing(self, clean_cli_env,
                                                     monkeypatch, galleries,
                                                     capsys):
        out = clean_cli_env / "out"
        gallery_dir = make_user_dir(
            out, "alice", content=json.dumps({"ABCD1234": "My Art_abcd1234.png"}))
        (gallery_dir / "My Art_abcd1234.png").write_bytes(b"x")
        galleries["alice"] = [
            make_dev(),
            make_dev(deviationid="ffffeeee-0000", title="New Work"),
        ]

        set_argv(monkeypatch, "-o", str(out), "--client-id", "x",
                 "--client-secret", "y", "--delay", "0")
        dd.run()

        assert (gallery_dir / "New Work_ffffeeee.png").is_file()
        stdout = capsys.readouterr().out
        assert "Downloaded: 1" in stdout
        assert "Skipped (already existed): 1" in stdout


class TestMain:
    def test_api_error_exits_with_message(self, monkeypatch):
        def boom():
            raise dd.ApiError("rate limited forever")

        monkeypatch.setattr(dd, "run", boom)
        with pytest.raises(SystemExit, match="rate limited forever"):
            dd.main()

    def test_keyboard_interrupt_exits_130(self, monkeypatch, capsys):
        def interrupt():
            raise KeyboardInterrupt

        monkeypatch.setattr(dd, "run", interrupt)
        with pytest.raises(SystemExit) as excinfo:
            dd.main()
        assert excinfo.value.code == 130
