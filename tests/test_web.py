"""The website route: media URLs, normalization, routing and the client."""

import pytest

from deviantart_downloader import web as web_mod
from deviantart_downloader.constants import WEB_SUBDIR

from .conftest import (BASE_URI, WEB_ID, WEB_URL, FakeResponse, FakeSession, csrf_page,
                       blocked_web_item, make_dev, web_item)


class TestWebMediaUrl:
    def test_untransformed_fullview_is_the_original(self):
        url = web_mod.web_media_url(web_item()["media"])
        # fullview names token index 1 and no transformation
        assert url == f"{BASE_URI}?token=tok1"

    def test_transformed_fullview_uses_the_template(self):
        url = web_mod.web_media_url(blocked_web_item()["media"])
        assert url == (f"{BASE_URI}/v1/fill/w_564,h_484/"
                       "web_art_by_artist_dxxxxxx-fullview.jpg?token=tok0")

    def test_out_of_range_token_index_falls_back_to_the_first(self):
        media = web_item()["media"]
        media["types"][1]["r"] = 9
        assert web_mod.web_media_url(media) == f"{BASE_URI}?token=tok0"

    def test_missing_token_yields_a_bare_url(self):
        media = web_item()["media"]
        media["token"] = []
        assert web_mod.web_media_url(media) == BASE_URI

    @pytest.mark.parametrize("media", [
        {},                                          # no baseUri
        {"baseUri": BASE_URI, "types": []},          # no fullview size
    ])
    def test_unusable_media_returns_none(self, media):
        assert web_mod.web_media_url(media) is None


class TestNormalizeWebDeviation:
    def test_maps_an_image_entry(self):
        dev = web_mod.normalize_web_deviation(web_item())
        assert dev["deviationid"] == str(WEB_ID)
        assert dev["title"] == "Web Art"
        assert dev["url"] == WEB_URL
        assert dev["content"]["src"] == f"{BASE_URI}?token=tok1"
        assert dev["_source"] == WEB_SUBDIR
        assert dev["is_mature"] is False

    def test_literature_has_no_media(self):
        dev = web_mod.normalize_web_deviation(web_item(type="literature"))
        assert dev["content"] is None

    def test_keeps_block_information(self):
        dev = web_mod.normalize_web_deviation(blocked_web_item())
        assert dev["is_blocked"] is True
        assert dev["block_reasons"] == ["mature_filter", "mature_loggedout"]


class TestNeedsApi:
    def test_plain_web_work_stays_on_the_website(self):
        assert web_mod.needs_api(web_mod.normalize_web_deviation(web_item())) is False

    def test_blocked_work_goes_to_the_api(self):
        assert web_mod.needs_api(web_mod.normalize_web_deviation(blocked_web_item())) is True

    def test_mature_without_media_goes_to_the_api(self):
        dev = web_mod.normalize_web_deviation(web_item(isMature=True, type="literature"))
        assert web_mod.needs_api(dev) is True

    def test_api_sourced_work_always_uses_the_api(self):
        assert web_mod.needs_api(make_dev()) is True


class TestWebClient:
    def make(self, responses):
        web = web_mod.WebClient()
        web.session = FakeSession(get_responses=responses)
        return web

    def test_reads_a_gallery_page(self):
        web = self.make([csrf_page(), FakeResponse(200, {"results": [web_item()]})])
        data = web.gallery_page("artist", 0, 60)
        assert len(data["results"]) == 1
        url, kwargs = web.session.get_calls[1]
        assert url == web_mod.GALLECTION_URL
        assert kwargs["params"]["csrf_token"] == "csrf-123"
        assert kwargs["params"]["offset"] == 0
        assert kwargs["params"]["username"] == "artist"

    def test_csrf_is_fetched_once_and_reused(self):
        web = self.make([csrf_page(),
                         FakeResponse(200, {"results": []}),
                         FakeResponse(200, {"results": []})])
        web.gallery_page("artist", 0, 60)
        web.gallery_page("artist", 60, 60)
        assert len(web.session.get_calls) == 3

    def test_stale_csrf_is_renewed_and_the_page_retried(self):
        web = self.make([csrf_page("old"),
                         FakeResponse(400, {"errorCode": 400}),
                         csrf_page("fresh"),
                         FakeResponse(200, {"results": [web_item()]})])
        data = web.gallery_page("artist", 0, 60)
        assert len(data["results"]) == 1
        assert web.session.get_calls[-1][1]["params"]["csrf_token"] == "fresh"

    def test_missing_csrf_raises_web_error(self):
        web = self.make([FakeResponse(200, text="<html>nothing here</html>")])
        with pytest.raises(web_mod.WebError, match="no CSRF token"):
            web.gallery_page("artist", 0, 60)

    def test_profile_error_raises_web_error(self):
        web = self.make([FakeResponse(404)])
        with pytest.raises(web_mod.WebError, match="HTTP 404"):
            web.gallery_page("artist", 0, 60)

    def test_listing_error_raises_web_error(self):
        web = self.make([csrf_page(), FakeResponse(500)])
        with pytest.raises(web_mod.WebError, match="HTTP 500"):
            web.gallery_page("artist", 0, 60)

    def test_persistent_rejection_gives_up(self):
        web = self.make([csrf_page(), FakeResponse(400)] * 3)
        with pytest.raises(web_mod.WebError, match="kept rejecting"):
            web.gallery_page("artist", 0, 60)
