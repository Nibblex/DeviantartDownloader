"""Walking gallery listings over both routes, and pairing them up."""

import pytest

from deviantart_downloader import manifest as manifest_mod
from deviantart_downloader import listing
from deviantart_downloader import web as web_mod
from deviantart_downloader.constants import CANCEL, WEB_SUBDIR
from deviantart_downloader.naming import deviation_key

from .conftest import (DEV_ID, WEB_ID, FakeClient, FakeWebClient,
                       blocked_web_item, make_dev, web_item)


class TestListingStopsOnQuit:
    def test_fetch_gallery_stops_when_cancelled(self):
        CANCEL.set()
        client = FakeClient(pages=[{"results": [make_dev()], "has_more": True}])
        assert listing.fetch_gallery(client, "artist") == []
        assert client.calls == []                 # never hit the network

    def test_fetch_gallery_web_stops_when_cancelled(self):
        CANCEL.set()
        web = FakeWebClient(pages=[{"results": [web_item()], "hasMore": True}])
        assert listing.fetch_gallery_web(web, "artist") == []
        assert web.calls == []

    def test_resolve_via_api_stops_when_cancelled(self, manifest):
        CANCEL.set()
        client = FakeClient(pages=[{"results": [make_dev()], "has_more": False}])
        blocked = [make_dev(deviationid="ffffeeee-0000",
                            url="https://www.deviantart.com/a/art/x-222")]
        result = listing.resolve_via_api(client, "artist", blocked,
                                         manifest=manifest, redownload_missing=True)
        assert result == []
        assert client.calls == []


def test_fetch_gallery_walks_every_page(capsys):
    client = FakeClient(pages=[
        {"results": [{"deviationid": "1"}], "has_more": True, "next_offset": 24},
        {"results": [{"deviationid": "2"}], "has_more": False},
    ])
    deviations = listing.fetch_gallery(client, "artist")
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
        manifest = manifest_mod.DownloadManifest(tmp_path)
        manifest.add(DEV_ID, "My Art_abcd1234.png")
        client = FakeClient(pages=self.make_pages())
        deviations = listing.fetch_gallery(client, "artist", manifest=manifest)
        assert [d["deviationid"] for d in deviations] == [DEV_ID]
        assert len(client.calls) == 1
        assert "stopping the listing early" in capsys.readouterr().out

    def test_full_walks_past_downloaded_pages(self, tmp_path):
        manifest = manifest_mod.DownloadManifest(tmp_path)
        manifest.add(DEV_ID, "My Art_abcd1234.png")
        client = FakeClient(pages=self.make_pages())
        deviations = listing.fetch_gallery(client, "artist", manifest=manifest,
                                      full=True)
        assert len(deviations) == 2

    def test_no_manifest_walks_every_page(self, capsys):
        client = FakeClient(pages=self.make_pages())
        deviations = listing.fetch_gallery(client, "artist")
        assert len(deviations) == 2

    def test_keeps_walking_while_a_work_is_unrecorded(self, tmp_path):
        # A failed download is never recorded in the manifest, so its page
        # keeps the walk going until the work is retried successfully.
        manifest = manifest_mod.DownloadManifest(tmp_path)
        manifest.add(DEV_ID, "My Art_abcd1234.png")
        client = FakeClient(pages=[
            {"results": [make_dev(), make_dev(deviationid="99999999-0000")],
             "has_more": True, "next_offset": 24},
            {"results": [make_dev(deviationid="ffffeeee-0000")], "has_more": False},
        ])
        deviations = listing.fetch_gallery(client, "artist", manifest=manifest)
        assert len(deviations) == 3

    def test_page_without_ids_does_not_stop(self, tmp_path):
        manifest = manifest_mod.DownloadManifest(tmp_path)
        client = FakeClient(pages=[
            {"results": [{"title": "no id"}], "has_more": True, "next_offset": 24},
            {"results": [make_dev(deviationid="ffffeeee-0000")], "has_more": False},
        ])
        deviations = listing.fetch_gallery(client, "artist", manifest=manifest)
        assert len(deviations) == 2


class TestFetchGalleryWeb:
    def test_walks_every_page_and_normalizes(self, capsys):
        web = FakeWebClient(pages=[
            {"results": [web_item()], "hasMore": True, "nextOffset": 60},
            {"results": [web_item(deviationId=2, url="x/art/b-2")], "hasMore": False},
        ])
        deviations = listing.fetch_gallery_web(web, "artist")
        assert [d["deviationid"] for d in deviations] == [str(WEB_ID), "2"]
        assert all(d["_source"] == WEB_SUBDIR for d in deviations)
        assert [c[1] for c in web.calls] == [0, 60]

    def test_stops_at_a_fully_downloaded_page(self, tmp_path, capsys):
        manifest = manifest_mod.DownloadManifest(tmp_path)
        manifest.add(str(WEB_ID), "web/Web Art_1004952679.jpg")
        web = FakeWebClient(pages=[
            {"results": [web_item()], "hasMore": True, "nextOffset": 60},
            {"results": [web_item(deviationId=2, url="x/art/b-2")], "hasMore": False},
        ])
        deviations = listing.fetch_gallery_web(web, "artist", manifest=manifest)
        assert len(deviations) == 1
        assert "stopping the listing early" in capsys.readouterr().out

    def test_full_walks_past_downloaded_pages(self, tmp_path):
        manifest = manifest_mod.DownloadManifest(tmp_path)
        manifest.add(str(WEB_ID), "web/Web Art_1004952679.jpg")
        web = FakeWebClient(pages=[
            {"results": [web_item()], "hasMore": True, "nextOffset": 60},
            {"results": [web_item(deviationId=2, url="x/art/b-2")], "hasMore": False},
        ])
        assert len(listing.fetch_gallery_web(web, "artist", manifest=manifest,
                                        full=True)) == 2


class TestListGallery:
    def test_prefers_the_website(self):
        web = FakeWebClient(pages=[{"results": [web_item()], "hasMore": False}])
        client = FakeClient()
        devs, from_web = listing.list_gallery(client, web, "artist",
                                         manifest=None, full=False)
        assert from_web is True
        assert len(devs) == 1
        assert client.calls == []

    def test_falls_back_to_the_api_when_the_website_breaks(self, capsys):
        class BrokenWeb(FakeWebClient):
            def gallery_page(self, username, offset, limit, folderid=None):
                raise web_mod.WebError("endpoint moved")

        client = FakeClient(pages=[{"results": [make_dev()], "has_more": False}])
        devs, from_web = listing.list_gallery(client, BrokenWeb(), "artist",
                                         manifest=None, full=False)
        assert from_web is False
        assert len(devs) == 1
        assert "falling back to the API" in capsys.readouterr().out

    def test_api_only_never_touches_the_website(self):
        client = FakeClient(pages=[{"results": [make_dev()], "has_more": False}])
        devs, from_web = listing.list_gallery(client, None, "artist",
                                         manifest=None, full=False)
        assert from_web is False
        assert len(devs) == 1


class TestFolderResolution:
    def api_folders_client(self, extra_pages=()):
        pages = [{"results": [
            {"folderid": "UUID-SKETCH", "name": "Sketches"},
            {"folderid": "UUID-FAN", "name": "Fan Art"},
        ], "has_more": False}]
        pages.extend(extra_pages)
        return FakeClient(pages=pages)

    def test_resolve_folder_api_matches_case_insensitively(self):
        client = self.api_folders_client()
        assert listing.resolve_folder_api(client, "artist", " sKeTcHeS ") == "UUID-SKETCH"
        assert client.calls[0][0] == "gallery/folders"

    def test_resolve_folder_api_unknown_name_lists_the_options(self):
        client = self.api_folders_client()
        with pytest.raises(listing.GalleryNotFoundError, match='"Sketches", "Fan Art"'):
            listing.resolve_folder_api(client, "artist", "Nope")

    def test_resolve_folder_web_returns_the_numeric_id(self):
        web = FakeWebClient(folders=[{"folderId": 99, "name": "Sketches"}])
        assert listing.resolve_folder_web(web, "artist", "sketches") == 99

    def test_resolve_folder_web_unknown_name_raises(self):
        web = FakeWebClient(folders=[{"folderId": 99, "name": "Sketches"}])
        with pytest.raises(listing.GalleryNotFoundError, match="Nope"):
            listing.resolve_folder_web(web, "artist", "Nope")

    def test_fetch_gallery_targets_the_folder_endpoint(self):
        client = FakeClient(pages=[{"results": [make_dev()], "has_more": False}])
        listing.fetch_gallery(client, "artist", folder="UUID-X")
        assert client.calls[0][0] == "gallery/UUID-X"

    def test_gallery_name_lists_only_that_folder_on_the_web(self):
        web = FakeWebClient(pages=[{"results": [web_item()], "hasMore": False}],
                            folders=[{"folderId": 99, "name": "Sketches"}])
        devs, from_web = listing.list_gallery(FakeClient(), web, "artist",
                                         manifest=None, full=False, gallery="sketches")
        assert from_web is True and len(devs) == 1
        assert web.calls[0][3] == 99   # the folderId reached gallery_page

    def test_gallery_name_lists_only_that_folder_on_the_api(self):
        client = FakeClient(pages=[
            {"results": [{"folderid": "UUID", "name": "Sketches"}], "has_more": False},
            {"results": [make_dev()], "has_more": False},
        ])
        devs, from_web = listing.list_gallery(client, None, "artist",
                                         manifest=None, full=False, gallery="Sketches")
        assert from_web is False and len(devs) == 1
        assert client.calls[0][0] == "gallery/folders"
        assert client.calls[1][0] == "gallery/UUID"


class TestResolveViaApi:
    def blocked(self):
        return web_mod.normalize_web_deviation(blocked_web_item())

    def test_matches_blocked_works_against_the_api_listing(self, tmp_path, capsys):
        manifest = manifest_mod.DownloadManifest(tmp_path)
        blocked = self.blocked()
        api_entry = make_dev(url=blocked["url"], title="Mature Art")
        client = FakeClient(pages=[{"results": [api_entry], "has_more": False}])
        resolved = listing.resolve_via_api(client, "artist", [blocked], [blocked],
                                      manifest=manifest, redownload_missing=False)
        assert resolved == [api_entry]

    def test_fetches_only_the_page_holding_the_blocked_work(self, tmp_path):
        # A single mature work sitting deep in the listing is looked up from the
        # one API page its position points at, not by walking the whole gallery.
        manifest = manifest_mod.DownloadManifest(tmp_path)
        blocked = self.blocked()
        api_entry = make_dev(url=blocked["url"], title="Mature Art")
        # 50 works ahead of it: position 50 -> API offset (50 // 24) * 24 == 48.
        ordered = [make_dev(deviationid=str(i), url=f"x/art/a-{i}")
                   for i in range(50)] + [blocked]
        client = FakeClient(pages=[{"results": [api_entry], "has_more": True}])
        resolved = listing.resolve_via_api(client, "artist", [blocked], ordered,
                                      manifest=manifest, redownload_missing=False)
        assert resolved == [api_entry]
        assert len(client.calls) == 1
        assert client.calls[0][1]["offset"] == 48

    def test_gap_fill_walks_when_positions_do_not_line_up(self, tmp_path):
        # The website order puts the work on page 0, but the API only serves it
        # on a later page: the walk fills the gap instead of giving up.
        manifest = manifest_mod.DownloadManifest(tmp_path)
        blocked = self.blocked()
        api_entry = make_dev(url=blocked["url"], title="Mature Art")
        client = FakeClient(pages=[
            {"results": [make_dev(deviationid="x", url="x/art/x-1")],
             "has_more": True, "next_offset": 24},
            {"results": [api_entry], "has_more": False},
        ])
        resolved = listing.resolve_via_api(client, "artist", [blocked], [blocked],
                                      manifest=manifest, redownload_missing=False)
        assert resolved == [api_entry]
        assert [c[1]["offset"] for c in client.calls] == [0, 24]

    def test_no_api_call_when_everything_is_downloaded(self, tmp_path):
        manifest = manifest_mod.DownloadManifest(tmp_path)
        blocked = self.blocked()
        manifest.add(deviation_key(blocked), "api/Mature Art_222222222.jpg")
        client = FakeClient()
        assert listing.resolve_via_api(client, "artist", [blocked], [blocked],
                                  manifest=manifest, redownload_missing=False) == []
        assert client.calls == []

    def test_warns_about_works_the_api_listing_did_not_return(self, tmp_path, capsys):
        manifest = manifest_mod.DownloadManifest(tmp_path)
        blocked = self.blocked()
        client = FakeClient(pages=[{"results": [], "has_more": False}])
        assert listing.resolve_via_api(client, "artist", [blocked], [blocked],
                                  manifest=manifest, redownload_missing=False) == []
        assert "were not in the API listing" in capsys.readouterr().out
