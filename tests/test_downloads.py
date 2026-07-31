"""Writing a work to disk, and everything that decides where it goes."""

import json

import pytest
import requests

from deviantart_downloader import downloads
from deviantart_downloader import web as web_mod
from deviantart_downloader.constants import CANCEL, RESUME

from .conftest import (BASE_URI, DEV_ID, FakeClient, FakeResponse, FakeSession,
                       fake_download, make_dev, recording_download)


def _tiptap(text):
    """A minimal tiptap `html` object whose body is a single line of text."""
    return {"type": "tiptap", "markup": json.dumps(
        {"document": {"content": [
            {"type": "paragraph", "content": [{"type": "text", "text": text}]}]}})}


class FakeWeb:
    """Stand-in WebClient exposing only deviation_text, for text works."""

    def __init__(self, text_content):
        self.text_content = text_content
        self.calls = []

    def deviation_text(self, deviationid, username):
        self.calls.append((deviationid, username))
        return self.text_content


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
        # A server that answers is not asked again: a 404 is not a connection
        # that gave way, and asking twice would only spend the request.
        assert len(session.get_calls) == 1

    def test_cancel_aborts_mid_download(self, tmp_path):
        session = FakeSession(get_responses=[
            FakeResponse(200, chunks=[b"abc", b"def"]),
        ])
        dest = tmp_path / "pic.png"
        CANCEL.set()
        assert downloads.download_file(session, "https://x/pic.png", dest) is False
        assert not dest.exists()
        assert not list(tmp_path.glob("*.part"))


class TestInterruptedTransfers:
    """A pause or a dropped connection is picked up, not started over."""

    def pausing(self, chunks):
        """Chunks that pause the run after the first, as pressing 'p' does."""
        def gen():
            for index, chunk in enumerate(chunks):
                if index:
                    RESUME.clear()
                yield chunk
        return gen()

    def resume_on_wait(self, monkeypatch):
        """Stand in for the user pressing 'r' while the run waits."""
        monkeypatch.setattr(downloads, "wait_if_paused", lambda: RESUME.set())

    def test_a_pause_lets_go_of_the_socket_and_picks_up_by_range(
            self, tmp_path, monkeypatch):
        first = FakeResponse(200, chunks=self.pausing([b"abc", b"def"]))
        rest = FakeResponse(206, chunks=[b"def"])
        session = FakeSession(get_responses=[first, rest])
        self.resume_on_wait(monkeypatch)
        dest = tmp_path / "pic.png"

        assert downloads.download_file(session, "https://x/pic.png", dest) is True
        # The chunk in hand when the pause came is fetched again, not lost.
        assert dest.read_bytes() == b"abcdef"
        assert "Range" not in session.get_calls[0][1].get("headers", {})
        assert session.get_calls[1][1]["headers"] == {"Range": "bytes=3-"}
        assert not list(tmp_path.glob("*.part"))

    def test_a_server_that_ignores_the_range_starts_over(self, tmp_path,
                                                         monkeypatch):
        # Answering 200 means the whole file is coming: appending it to what is
        # already here would splice one onto the other.
        first = FakeResponse(200, chunks=self.pausing([b"abc", b"def"]))
        whole = FakeResponse(200, chunks=[b"abcdef"])
        session = FakeSession(get_responses=[first, whole])
        self.resume_on_wait(monkeypatch)
        dest = tmp_path / "pic.png"

        assert downloads.download_file(session, "https://x/pic.png", dest) is True
        assert dest.read_bytes() == b"abcdef"

    def test_a_lost_connection_is_picked_up_from_what_is_on_disk(
            self, tmp_path, capsys):
        def dies():
            yield b"abc"
            raise requests.ConnectionError("connection reset")

        session = FakeSession(get_responses=[
            FakeResponse(200, chunks=dies()),
            FakeResponse(206, chunks=[b"def"]),
        ])
        dest = tmp_path / "pic.png"
        assert downloads.download_file(session, "https://x/pic.png", dest) is True
        assert dest.read_bytes() == b"abcdef"
        assert session.get_calls[1][1]["headers"] == {"Range": "bytes=3-"}
        assert "picking it up again" in capsys.readouterr().out

    def test_it_gives_up_after_a_few_goes(self, tmp_path, capsys):
        def dies():
            yield b"abc"
            raise requests.ConnectionError("connection reset")

        session = FakeSession(get_responses=[
            FakeResponse(200, chunks=dies()) for _ in range(4)])
        dest = tmp_path / "pic.png"
        assert downloads.download_file(session, "https://x/pic.png", dest) is False
        # The first go plus STREAM_RETRIES more, and nothing left behind.
        assert len(session.get_calls) == downloads.STREAM_RETRIES + 1
        assert not dest.exists() and not list(tmp_path.glob("*.part"))
        assert "ERROR" in capsys.readouterr().out

    def test_quitting_while_paused_leaves_nothing_behind(self, tmp_path,
                                                         monkeypatch):
        session = FakeSession(get_responses=[
            FakeResponse(200, chunks=self.pausing([b"abc", b"def"]))])
        # 'q' pressed while the run sat paused.
        monkeypatch.setattr(downloads, "wait_if_paused",
                            lambda: (RESUME.set(), CANCEL.set()))
        dest = tmp_path / "pic.png"
        assert downloads.download_file(session, "https://x/pic.png", dest) is False
        assert not dest.exists() and not list(tmp_path.glob("*.part"))


class TestProcessDeviation:
    def test_an_impossible_blur_is_clamped_before_the_request(self, tmp_path,
                                                              manifest, monkeypatch):
        """blur_171 answers 400 on both routes; blur_100 is what can be fetched."""
        blurred = ("https://images-wixmp-abc.wixmp.com/f/uuid/file.jpg"
                   "/v1/fill/w_4000,h_3000,q_75,strp,blur_171/x-fullview.jpg?token=t")
        fetched = []
        monkeypatch.setattr(downloads, "download_file",
                            lambda session, url, dest, fallback=None:
                            (fetched.append((url, fallback)),
                             dest.write_bytes(b"x"), True)[2])
        status, _ = downloads.process_deviation(
            FakeClient(), make_dev(content={"src": blurred}), tmp_path, manifest)
        assert status == "downloaded"
        assert "blur_100" in fetched[0][0] and "blur_171" not in fetched[0][0]

    def test_the_unblur_fallback_is_clamped_too(self, tmp_path, manifest,
                                                monkeypatch):
        """--unblur strips the blur, the CDN 403s, and the fallback must work."""
        blurred = ("https://images-wixmp-abc.wixmp.com/f/uuid/file.jpg"
                   "/v1/fill/w_4000,h_3000,strp,blur_171/x-fullview.jpg?token=t")
        fetched = []
        monkeypatch.setattr(downloads, "download_file",
                            lambda session, url, dest, fallback=None:
                            (fetched.append((url, fallback)),
                             dest.write_bytes(b"x"), True)[2])
        downloads.process_deviation(
            FakeClient(), make_dev(content={"src": blurred}), tmp_path, manifest,
            unblur=True)
        url, fallback = fetched[0]
        assert "blur_" not in url                  # --unblur stripped it
        assert "blur_100" in fallback              # and the retry is valid


    def test_downloads_content_src(self, tmp_path, manifest, monkeypatch):
        fetched = []

        def fake_download(session, url, dest, fallback=None):
            fetched.append((url, fallback))
            dest.write_bytes(b"x")
            return True

        monkeypatch.setattr(downloads, "download_file", fake_download)
        status, msg = downloads.process_deviation(
            FakeClient(), make_dev(), tmp_path, manifest)
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
            client, make_dev(is_downloadable=True), tmp_path, manifest)
        assert status == "downloaded"
        assert fetched == ["https://example.com/original.png"]
        assert client.calls[0][0] == f"deviation/download/{DEV_ID}"

    def test_the_fullview_being_the_original_spends_no_request(
            self, tmp_path, manifest, monkeypatch):
        """The listing says what the original weighs; the CDN says what this does.

        Most works are handed back unchanged by the download endpoint, so the
        request it costs is worth avoiding when the answer is already on disk's
        doorstep.
        """
        client = FakeClient()                       # no page queued: a call would raise
        fetched = []
        monkeypatch.setattr(downloads, "download_file", recording_download(fetched))
        monkeypatch.setattr(downloads, "remote_size", lambda session, url: 4096)
        status, _ = downloads.process_deviation(
            client, make_dev(is_downloadable=True, download_filesize=4096),
            tmp_path, manifest)
        assert status == "downloaded"
        assert [url for url, _ in fetched] == ["https://example.com/pic.png"]
        assert client.calls == []

    @pytest.mark.parametrize("remote,size", [
        (2048, 4096),      # the fullview was re-encoded smaller
        (None, 4096),      # the CDN would not say
        (4096, None),      # the listing does not know the original's size
        (4096, 0),         # nor when it says nothing useful
    ])
    def test_anything_short_of_a_match_still_asks_for_the_original(
            self, tmp_path, manifest, monkeypatch, remote, size):
        client = FakeClient(pages=[{"src": "https://example.com/original.png"}])
        fetched = []
        monkeypatch.setattr(downloads, "download_file", recording_download(fetched))
        monkeypatch.setattr(downloads, "remote_size", lambda session, url: remote)
        downloads.process_deviation(
            client, make_dev(is_downloadable=True, download_filesize=size),
            tmp_path, manifest)
        assert client.calls[0][0] == f"deviation/download/{DEV_ID}"
        assert [url for url, _ in fetched] == ["https://example.com/original.png"]

    def test_a_blur_is_not_even_measured(self, tmp_path, manifest, monkeypatch):
        """The placeholder is a different, smaller file: it could never match,
        so the head request would be a round trip spent to learn nothing."""
        client = FakeClient(pages=[{"src": "https://example.com/original.png"}])
        blurred = ("https://images-wixmp-a.wixmp.com/f/u/x.jpg"
                   "/v1/fill/w_300,h_200,q_70,strp,blur_60/x-fullview.jpg?token=t")
        monkeypatch.setattr(downloads, "download_file", fake_download)
        monkeypatch.setattr(downloads, "remote_size",
                            lambda session, url: pytest.fail("no head on a blur"))
        downloads.process_deviation(
            client, make_dev(is_downloadable=True, download_filesize=4096,
                             content={"src": blurred}),
            tmp_path, manifest)
        # It still asks for the original, as it always had to.
        assert client.calls[0][0] == f"deviation/download/{DEV_ID}"

    def test_the_website_route_never_asks_either_way(self, tmp_path, manifest,
                                                     monkeypatch):
        client = FakeClient()
        monkeypatch.setattr(downloads, "download_file", fake_download)
        monkeypatch.setattr(downloads, "remote_size",
                            lambda session, url: pytest.fail("no head needed"))
        downloads.process_deviation(
            client, make_dev(is_downloadable=True, download_filesize=4096),
            tmp_path, manifest, use_api=False)
        assert client.calls == []

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
            FailingClient(), make_dev(is_downloadable=True), tmp_path, manifest)
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
            FakeClient(), make_dev(content={"src": blurred}), tmp_path,
            manifest, unblur=True)
        assert status == "downloaded"
        url, fallback = fetched[0]
        assert ",blur_16" not in url
        assert fallback == blurred

    def test_skips_already_downloaded(self, tmp_path, manifest):
        manifest.add(DEV_ID, "old name.png")
        (tmp_path / "old name.png").write_bytes(b"x")
        status, msg = downloads.process_deviation(
            FakeClient(), make_dev(), tmp_path, manifest)
        assert status == "skipped"
        assert "old name.png" in msg

    def test_skips_locally_deleted_by_default(self, tmp_path, manifest):
        manifest.add(DEV_ID, "deleted.png")
        status, msg = downloads.process_deviation(
            FakeClient(), make_dev(), tmp_path, manifest)
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
            FakeClient(), make_dev(), tmp_path, manifest,
            redownload_missing=True)
        assert status == "downloaded"

    def test_existing_file_with_same_name_is_recorded(self, tmp_path, manifest):
        (tmp_path / "My Art_abcd1234.png").write_bytes(b"x")
        status, _ = downloads.process_deviation(
            FakeClient(), make_dev(), tmp_path, manifest)
        assert status == "skipped"
        assert manifest.has(DEV_ID)

    def test_no_media_deviation(self, tmp_path, manifest):
        status, msg = downloads.process_deviation(
            FakeClient(), make_dev(content=None), tmp_path, manifest)
        assert status == "no_media"
        assert "NO FILE" in msg

    def test_failed_download(self, tmp_path, manifest, monkeypatch):
        monkeypatch.setattr(downloads, "download_file",
                            lambda session, url, dest, fallback=None: False)
        status, _ = downloads.process_deviation(
            FakeClient(), make_dev(), tmp_path, manifest)
        assert status == "failed"
        assert not manifest.has(DEV_ID)

    def test_cancelled_before_start(self, tmp_path, manifest):
        CANCEL.set()
        status, _ = downloads.process_deviation(
            FakeClient(), make_dev(), tmp_path, manifest)
        assert status == "cancelled"


class TestLiteratureDownload:
    def _lit_dev(self, **overrides):
        dev = {
            "deviationid": "1260299235",
            "title": "My Poem",
            "url": "https://www.deviantart.com/artist/art/My-Poem-1260299235",
            "type": "literature",
            "content": None,
            "excerpt": "short excerpt",
        }
        dev.update(overrides)
        return dev

    def test_web_route_writes_the_full_body(self, tmp_path, manifest):
        web = FakeWeb({"html": _tiptap("Full body"), "excerpt": "short excerpt"})
        status, msg = downloads.process_deviation(
            FakeClient(), self._lit_dev(), tmp_path, manifest,
            dest_dir=tmp_path / "web", use_api=False, web=web)
        assert status == "downloaded"
        assert "text" in msg
        dest = tmp_path / "web" / "My Poem_1260299235.txt"
        assert dest.read_text(encoding="utf-8") == "Full body\n"
        assert manifest.filename_for("1260299235") == "web/My Poem_1260299235.txt"

    def test_web_route_writes_an_html_document(self, tmp_path, manifest):
        web = FakeWeb({"html": _tiptap("Full body")})
        status, _ = downloads.process_deviation(
            FakeClient(), self._lit_dev(), tmp_path, manifest,
            dest_dir=tmp_path / "web", use_api=False, web=web, text_format="html")
        assert status == "downloaded"
        dest = tmp_path / "web" / "My Poem_1260299235.html"
        content = dest.read_text(encoding="utf-8")
        assert content.startswith("<!DOCTYPE html>")
        assert "<title>My Poem</title>" in content
        assert "<p>Full body</p>" in content
        assert manifest.filename_for("1260299235") == "web/My Poem_1260299235.html"

    def test_web_route_falls_back_to_excerpt_when_body_empty(self, tmp_path, manifest):
        web = FakeWeb({"html": {}, "excerpt": "just the excerpt"})
        status, _ = downloads.process_deviation(
            FakeClient(), self._lit_dev(), tmp_path, manifest,
            dest_dir=tmp_path / "web", use_api=False, web=web)
        assert status == "downloaded"
        dest = tmp_path / "web" / "My Poem_1260299235.txt"
        assert dest.read_text(encoding="utf-8") == "just the excerpt\n"

    def test_web_error_falls_back_to_the_listing_excerpt(self, tmp_path, manifest):
        class Boom(FakeWeb):
            def deviation_text(self, deviationid, username):
                raise web_mod.WebError("unavailable")

        status, _ = downloads.process_deviation(
            FakeClient(), self._lit_dev(), tmp_path, manifest,
            dest_dir=tmp_path / "web", use_api=False, web=Boom(None))
        assert status == "downloaded"
        dest = tmp_path / "web" / "My Poem_1260299235.txt"
        assert dest.read_text(encoding="utf-8") == "short excerpt\n"

    def test_api_route_uses_the_content_endpoint(self, tmp_path, manifest):
        dev = {"deviationid": DEV_ID, "title": "Api Lit", "url": "",
               "excerpt": "fallback", "content": None}
        client = FakeClient(pages=[{"html": "<p>API body</p>"}])
        status, _ = downloads.process_deviation(
            client, dev, tmp_path, manifest,
            dest_dir=tmp_path / "api", use_api=True)
        assert status == "downloaded"
        assert client.calls[0][0] == "deviation/content"
        dest = tmp_path / "api" / f"Api Lit_{DEV_ID[:8]}.txt"
        assert dest.read_text(encoding="utf-8") == "API body\n"

    def test_api_route_falls_back_to_excerpt(self, tmp_path, manifest):
        dev = {"deviationid": DEV_ID, "title": "Api Lit", "url": "",
               "excerpt": "fallback excerpt", "content": None}
        client = FakeClient(pages=[{"html": ""}])       # editor format: empty
        status, _ = downloads.process_deviation(
            client, dev, tmp_path, manifest,
            dest_dir=tmp_path / "api", use_api=True)
        assert status == "downloaded"
        dest = tmp_path / "api" / f"Api Lit_{DEV_ID[:8]}.txt"
        assert dest.read_text(encoding="utf-8") == "fallback excerpt\n"

    def test_no_text_anywhere_is_no_media(self, tmp_path, manifest):
        dev = {"deviationid": DEV_ID, "title": "Nothing", "url": "",
               "type": "literature", "content": None}
        client = FakeClient(pages=[{"html": ""}])
        status, msg = downloads.process_deviation(
            client, dev, tmp_path, manifest, use_api=True)
        assert status == "no_media"
        assert not manifest.has(DEV_ID)

    def test_rerun_skips_text_via_manifest(self, tmp_path, manifest):
        dev = self._lit_dev()
        web = FakeWeb({"html": _tiptap("Body")})
        downloads.process_deviation(FakeClient(), dev, tmp_path, manifest,
                                    dest_dir=tmp_path / "web", use_api=False, web=web)
        status, msg = downloads.process_deviation(
            FakeClient(), dev, tmp_path, manifest,
            dest_dir=tmp_path / "web", use_api=False, web=FakeWeb({"html": _tiptap("Body")}))
        assert status == "skipped"


class TestRedownloadBlurred:
    """Replace the blurred placeholder a logged-out run settled for."""

    BLURRED = f"{BASE_URI}/v1/fill/w_1080,h_927,q_75,strp,blur_46/x-fullview.jpg?token=t"
    CLEAN = f"{BASE_URI}?token=t"

    def downloaded(self, tmp_path, manifest, rel, size=100):
        """A work recorded in the manifest with its file sitting on disk."""
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"x" * size)
        manifest.add(DEV_ID, rel)
        return dest

    def sync(self, tmp_path, manifest, monkeypatch, src, remote=None,
             subdir="api", **kw):
        """One work through the route that wrote it, as sync_gallery drives it."""
        fetched = []
        monkeypatch.setattr(downloads, "download_file", recording_download(fetched))
        monkeypatch.setattr(downloads, "remote_size", lambda session, url: remote)
        status, msg = downloads.process_deviation(
            FakeClient(), make_dev(content={"src": src}), tmp_path, manifest,
            use_api=True, dest_dir=tmp_path / subdir, **kw)
        return status, msg, [url for url, _ in fetched]

    def test_a_blurred_copy_is_replaced_by_the_clean_one(self, tmp_path, manifest,
                                                        monkeypatch):
        old = self.downloaded(tmp_path, manifest, "api/My Art_abcd1234.png")
        status, msg, fetched = self.sync(tmp_path, manifest, monkeypatch, self.CLEAN,
                                         remote=999999, redownload_blurred=True)
        # Counted apart from a plain download so the run can report how many
        # blurred copies actually changed.
        assert status == "replaced" and "Replaced blurred copy" in msg
        assert fetched == [self.CLEAN]
        # The clean image resolved to .jpg where the blurred copy was .png, so
        # the old name must go rather than linger holding the blur.
        assert (tmp_path / "api" / "My Art_abcd1234.jpg").read_bytes() == b"x"
        assert not old.exists()
        assert manifest.filename_for(DEV_ID) == "api/My Art_abcd1234.jpg"

    def test_a_copy_that_is_already_clean_is_kept(self, tmp_path, manifest,
                                                  monkeypatch):
        """Same size means this one was already fetched unblurred."""
        self.downloaded(tmp_path, manifest, "api/My Art_abcd1234.png", size=100)
        status, msg, fetched = self.sync(tmp_path, manifest, monkeypatch, self.CLEAN,
                                         remote=100, redownload_blurred=True)
        assert status == "skipped" and "Already unblurred" in msg
        assert fetched == []

    def test_a_work_still_only_served_blurred_is_kept(self, tmp_path, manifest,
                                                      monkeypatch):
        """Without --login the API keeps offering the blur; refetching is moot."""
        self.downloaded(tmp_path, manifest, "api/My Art_abcd1234.png")
        status, msg, fetched = self.sync(tmp_path, manifest, monkeypatch, self.BLURRED,
                                         remote=999999, redownload_blurred=True)
        assert status == "skipped" and "Still only served blurred" in msg
        assert fetched == []
        # Decided off the listing, so no metered request was spent to learn it.
        assert FakeClient().calls == []

    def test_a_website_route_copy_is_never_touched(self, tmp_path, manifest,
                                                   monkeypatch):
        """The website serves what it serves in full, so it never blurred one."""
        self.downloaded(tmp_path, manifest, "web/My Art_abcd1234.png")
        status, msg, fetched = self.sync(tmp_path, manifest, monkeypatch, self.CLEAN,
                                         remote=999999, subdir="web",
                                         redownload_blurred=True)
        assert status == "skipped" and "Already exists" in msg
        assert fetched == []

    def test_an_unknown_remote_size_refetches(self, tmp_path, manifest, monkeypatch):
        """The CDN would not say; re-fetching is the safe way to be wrong."""
        self.downloaded(tmp_path, manifest, "api/My Art_abcd1234.png")
        status, _, fetched = self.sync(tmp_path, manifest, monkeypatch, self.CLEAN,
                                       remote=None, redownload_blurred=True)
        assert status == "replaced" and fetched == [self.CLEAN]
