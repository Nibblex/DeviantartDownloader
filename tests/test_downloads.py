"""Writing a work to disk, and everything that decides where it goes."""

import requests

from deviantart_downloader import downloads
from deviantart_downloader.constants import CANCEL

from .conftest import DEV_ID, FakeClient, FakeResponse, FakeSession, make_dev


class TestDownloadFile:
    def test_success_writes_file(self, tmp_path):
        session = FakeSession(get_responses=[
            FakeResponse(200, chunks=[b"abc", b"def"]),
        ])
        dest = tmp_path / "pic.png"
        assert downloads.download_file(session, "https://x/pic.png", dest) is True
        assert dest.read_bytes() == b"abcdef"
        assert not list(tmp_path.glob("*.part"))

    def test_403_falls_back_to_blurred_url(self, tmp_path, capsys):
        session = FakeSession(get_responses=[
            FakeResponse(403),
            FakeResponse(200, chunks=[b"blurred"]),
        ])
        dest = tmp_path / "pic.png"
        ok = downloads.download_file(session, "https://x/unblurred.png", dest,
                              fallback_url="https://x/blurred.png")
        assert ok is True
        assert dest.read_bytes() == b"blurred"
        assert session.get_calls[1][0] == "https://x/blurred.png"
        assert "Unblur rejected" in capsys.readouterr().out

    def test_http_error_returns_false_and_cleans_up(self, tmp_path, capsys):
        session = FakeSession(get_responses=[FakeResponse(404)])
        dest = tmp_path / "pic.png"
        assert downloads.download_file(session, "https://x/pic.png", dest) is False
        assert not dest.exists()
        assert not list(tmp_path.glob("*.part"))
        assert "ERROR" in capsys.readouterr().out

    def test_cancel_aborts_mid_download(self, tmp_path):
        session = FakeSession(get_responses=[
            FakeResponse(200, chunks=[b"abc", b"def"]),
        ])
        dest = tmp_path / "pic.png"
        CANCEL.set()
        assert downloads.download_file(session, "https://x/pic.png", dest) is False
        assert not dest.exists()
        assert not list(tmp_path.glob("*.part"))


class TestProcessDeviation:
    def test_downloads_content_src(self, tmp_path, manifest, monkeypatch):
        fetched = []

        def fake_download(session, url, dest, fallback=None):
            fetched.append((url, fallback))
            dest.write_bytes(b"x")
            return True

        monkeypatch.setattr(downloads, "download_file", fake_download)
        status, msg = downloads.process_deviation(
            FakeClient(), make_dev(), tmp_path, 0, manifest)
        assert status == "downloaded"
        assert fetched == [("https://example.com/pic.png", None)]
        assert manifest.filename_for(DEV_ID) == "My Art_abcd1234.png"
        assert (tmp_path / "My Art_abcd1234.png").is_file()

    def test_prefers_original_download_url(self, tmp_path, manifest, monkeypatch):
        client = FakeClient(pages=[{"src": "https://example.com/original.png"}])
        fetched = []

        def fake_download(session, url, dest, fallback=None):
            fetched.append(url)
            dest.write_bytes(b"x")
            return True

        monkeypatch.setattr(downloads, "download_file", fake_download)
        status, _ = downloads.process_deviation(
            client, make_dev(is_downloadable=True), tmp_path, 0, manifest)
        assert status == "downloaded"
        assert fetched == ["https://example.com/original.png"]
        assert client.calls[0][0] == f"deviation/download/{DEV_ID}"

    def test_falls_back_when_download_endpoint_fails(self, tmp_path, manifest,
                                                     monkeypatch):
        class FailingClient(FakeClient):
            def api_get(self, endpoint, params=None):
                raise requests.HTTPError("boom")

        fetched = []

        def fake_download(session, url, dest, fallback=None):
            fetched.append(url)
            dest.write_bytes(b"x")
            return True

        monkeypatch.setattr(downloads, "download_file", fake_download)
        status, _ = downloads.process_deviation(
            FailingClient(), make_dev(is_downloadable=True), tmp_path, 0, manifest)
        assert status == "downloaded"
        assert fetched == ["https://example.com/pic.png"]

    def test_unblur_passes_blurred_url_as_fallback(self, tmp_path, manifest,
                                                   monkeypatch):
        blurred = "https://images-wixmp-abc.wixmp.com/f/pic.png/v1/fill/w_1,blur_16/pic.png"
        fetched = []

        def fake_download(session, url, dest, fallback=None):
            fetched.append((url, fallback))
            dest.write_bytes(b"x")
            return True

        monkeypatch.setattr(downloads, "download_file", fake_download)
        status, _ = downloads.process_deviation(
            FakeClient(), make_dev(content={"src": blurred}), tmp_path, 0,
            manifest, unblur=True)
        assert status == "downloaded"
        url, fallback = fetched[0]
        assert ",blur_16" not in url
        assert fallback == blurred

    def _spy_delay(self, monkeypatch):
        """Record the delays passed to CANCEL.wait after a download."""
        waited = []
        monkeypatch.setattr(downloads, "download_file",
                            lambda session, url, dest, fallback=None:
                            (dest.write_bytes(b"x"), True)[1])
        monkeypatch.setattr(downloads.CANCEL, "wait",
                            lambda delay: waited.append(delay) or False)
        return waited

    def test_delay_throttles_the_api_route(self, tmp_path, manifest, monkeypatch):
        waited = self._spy_delay(monkeypatch)
        status, _ = downloads.process_deviation(
            FakeClient(), make_dev(), tmp_path, 0.5, manifest, use_api=True)
        assert status == "downloaded"
        assert waited == [0.5]

    def test_delay_skips_the_website_route(self, tmp_path, manifest, monkeypatch):
        waited = self._spy_delay(monkeypatch)
        status, _ = downloads.process_deviation(
            FakeClient(), make_dev(), tmp_path, 0.5, manifest, use_api=False)
        assert status == "downloaded"
        assert waited == []

    def test_skips_already_downloaded(self, tmp_path, manifest):
        manifest.add(DEV_ID, "old name.png")
        (tmp_path / "old name.png").write_bytes(b"x")
        status, msg = downloads.process_deviation(
            FakeClient(), make_dev(), tmp_path, 0, manifest)
        assert status == "skipped"
        assert "old name.png" in msg

    def test_skips_locally_deleted_by_default(self, tmp_path, manifest):
        manifest.add(DEV_ID, "deleted.png")
        status, msg = downloads.process_deviation(
            FakeClient(), make_dev(), tmp_path, 0, manifest)
        assert status == "skipped"
        assert "Deleted locally" in msg

    def test_redownload_missing_restores_deleted(self, tmp_path, manifest,
                                                 monkeypatch):
        manifest.add(DEV_ID, "deleted.png")

        def fake_download(session, url, dest, fallback=None):
            dest.write_bytes(b"x")
            return True

        monkeypatch.setattr(downloads, "download_file", fake_download)
        status, _ = downloads.process_deviation(
            FakeClient(), make_dev(), tmp_path, 0, manifest,
            redownload_missing=True)
        assert status == "downloaded"

    def test_existing_file_with_same_name_is_recorded(self, tmp_path, manifest):
        (tmp_path / "My Art_abcd1234.png").write_bytes(b"x")
        status, _ = downloads.process_deviation(
            FakeClient(), make_dev(), tmp_path, 0, manifest)
        assert status == "skipped"
        assert manifest.has(DEV_ID)

    def test_no_media_deviation(self, tmp_path, manifest):
        status, msg = downloads.process_deviation(
            FakeClient(), make_dev(content=None), tmp_path, 0, manifest)
        assert status == "no_media"
        assert "NO FILE" in msg

    def test_failed_download(self, tmp_path, manifest, monkeypatch):
        monkeypatch.setattr(downloads, "download_file",
                            lambda session, url, dest, fallback=None: False)
        status, _ = downloads.process_deviation(
            FakeClient(), make_dev(), tmp_path, 0, manifest)
        assert status == "failed"
        assert not manifest.has(DEV_ID)

    def test_cancelled_before_start(self, tmp_path, manifest):
        CANCEL.set()
        status, _ = downloads.process_deviation(
            FakeClient(), make_dev(), tmp_path, 0, manifest)
        assert status == "cancelled"
