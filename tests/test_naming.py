"""Usernames, file names and the deviation key shared by both routes."""

from datetime import datetime, timezone

import pytest

from deviantart_downloader import web as web_mod
from deviantart_downloader import naming
from deviantart_downloader.constants import WEB_SUBDIR

from .conftest import (API_USER_ID, DEV_ID, WEB_ID, WEB_URL, WEB_USER_ID,
                       make_dev, web_item)


class TestExtractUsername:
    @pytest.mark.parametrize("url,expected", [
        ("https://www.deviantart.com/someartist", "someartist"),
        ("https://www.deviantart.com/someartist/gallery/all", "someartist"),
        ("www.deviantart.com/someartist", "someartist"),
        ("https://someartist.deviantart.com", "someartist"),
        ("someartist", "someartist"),
    ])
    def test_valid_inputs(self, url, expected):
        assert naming.extract_username(url) == expected

    @pytest.mark.parametrize("url", [
        "https://www.deviantart.com",   # no username in the path
        "some.user",                    # dots are not allowed in bare names
        "https://example.com/whoever",  # not a DeviantArt URL
    ])
    def test_invalid_inputs_exit(self, url):
        with pytest.raises(SystemExit):
            naming.extract_username(url)


class TestSanitizeFilename:
    def test_replaces_forbidden_characters(self):
        assert naming.sanitize_filename('a<b>:c"d/e\\f|g?h*i') == "a_b__c_d_e_f_g_h_i"

    def test_strips_control_characters(self):
        assert naming.sanitize_filename("a\x00b\x1fc") == "a_b_c"

    def test_strips_leading_trailing_dots_and_spaces(self):
        assert naming.sanitize_filename("  .name.  ") == "name"

    def test_truncates_long_names(self):
        assert len(naming.sanitize_filename("x" * 300)) == 150

    @pytest.mark.parametrize("name", ["", "  ", "..."])
    def test_empty_becomes_untitled(self, name):
        assert naming.sanitize_filename(name) == "untitled"


class TestUnblurWixmpUrl:
    def test_strips_blur_from_wixmp_urls(self):
        url = "https://images-wixmp-abc.wixmp.com/f/x/y.png/v1/fill/w_1,h_1,q_80,blur_16/pic.png?token=t"
        assert ",blur_16" not in naming.unblur_wixmp_url(url)

    def test_only_first_blur_segment_is_removed(self):
        url = "https://images-wixmp-abc.wixmp.com/a,blur_16/b,blur_16/pic.png"
        assert naming.unblur_wixmp_url(url).count(",blur_16") == 1

    def test_non_wixmp_urls_are_untouched(self):
        url = "https://example.com/a,blur_16/pic.png"
        assert naming.unblur_wixmp_url(url) == url


class TestGuessExtension:
    @pytest.mark.parametrize("url,expected", [
        ("https://example.com/dir/pic.png", ".png"),
        ("https://example.com/dir/pic.JPEG?token=abc", ".jpeg"),
        ("https://example.com/dir/pic%20name.gif", ".gif"),
        ("https://example.com/dir/noext", ".jpg"),
        ("https://example.com/dir/weird.superlong", ".jpg"),
    ])
    def test_extensions(self, url, expected):
        assert naming.guess_extension(url) == expected


class TestDeviationKey:
    def test_prefers_the_numeric_id_in_the_url(self):
        assert naming.deviation_key(make_dev(url=WEB_URL)) == str(WEB_ID)
        assert naming.deviation_suffix(make_dev(url=WEB_URL)) == str(WEB_ID)

    def test_falls_back_to_the_uuid(self):
        assert naming.deviation_key(make_dev()) == DEV_ID
        assert naming.deviation_suffix(make_dev()) == "abcd1234"

    def test_both_routes_agree_on_the_same_work(self):
        from_web = web_mod.normalize_web_deviation(web_item())
        from_api = make_dev(url=WEB_URL)
        assert naming.deviation_key(from_web) == naming.deviation_key(from_api)

    def test_unidentifiable_work_has_no_key(self):
        assert naming.deviation_key({"title": "x"}) == ""


class TestUserIds:
    """The same problem as the deviation key, one level up: no shared field."""

    WEB_WORK = {"_source": WEB_SUBDIR,
                "author": {"userid": WEB_USER_ID, "username": "artist"}}

    def test_a_website_listing_reports_the_numeric_id(self):
        assert naming.user_ids([self.WEB_WORK]) == {"web": str(WEB_USER_ID)}

    def test_an_api_listing_reports_the_uuid(self):
        assert naming.user_ids([make_dev()]) == {"api": API_USER_ID}

    def test_a_listing_without_an_author_reports_nothing(self):
        assert naming.user_ids([{"_source": WEB_SUBDIR}, {}]) == {}

    def test_the_two_routes_are_kept_apart(self):
        # A run that used both routes learns one id per route, not one id: for
        # the same user the two values differ.
        assert naming.user_ids([self.WEB_WORK, make_dev()]) == {
            "web": str(WEB_USER_ID), "api": API_USER_ID}


class TestDeviationTime:
    """The routes disagree about the format, as they do about ids."""

    UTC = datetime(2023, 7, 10, 12, 34, 56, tzinfo=timezone.utc)

    @pytest.mark.parametrize("raw", [
        "1688992496",                 # the API: a Unix timestamp in a string
        "2023-07-10T12:34:56+00:00",  # ISO with an offset spelled out
        "2023-07-10T12:34:56Z",       # ...and the "Z" 3.10 will not parse
        "2023-07-10T12:34:56z",
        "2023-07-10T12:34:56",        # no offset at all: read as UTC
    ])
    def test_every_spelling_lands_on_the_same_instant(self, raw):
        assert naming.deviation_time({"published_time": raw}) == self.UTC

    def test_an_offset_without_a_colon_is_understood(self):
        # How the website writes it, and the other spelling 3.10 rejects.
        assert naming.deviation_time(
            {"published_time": "2023-07-10T12:34:56-0700"}
        ) == datetime(2023, 7, 10, 19, 34, 56, tzinfo=timezone.utc)

    def test_the_answer_is_always_aware(self):
        # A naive datetime cannot be compared with an aware one: it raises.
        for raw in ("1688992496", "2023-07-10T12:34:56", "2023-07-10T12:34:56-0700"):
            assert naming.deviation_time({"published_time": raw}).tzinfo is not None

    @pytest.mark.parametrize("dev", [
        {},                                     # the listing carried no date
        {"published_time": ""},
        {"published_time": None},
        {"published_time": "not a date"},
        {"published_time": "2023-13-45"},       # parses as neither
    ])
    def test_what_cannot_be_read_is_not_guessed(self, dev):
        assert naming.deviation_time(dev) is None

    def test_an_out_of_range_timestamp_is_not_fatal(self):
        assert naming.deviation_time({"published_time": "9" * 30}) is None


class TestClampWixmpBlur:
    """DeviantArt serves blurs its own CDN refuses; they answer 400 either way."""

    WIXMP = "https://images-wixmp-abc.wixmp.com/f/uuid/file.jpg"

    def test_an_out_of_range_blur_is_brought_down_to_the_maximum(self):
        url = f"{self.WIXMP}/v1/fill/w_4000,h_3000,q_75,strp,blur_171/x.jpg?token=t"
        assert naming.clamp_wixmp_blur(url) == url.replace("blur_171", "blur_100")

    def test_a_blur_the_cdn_accepts_is_left_alone(self):
        # The token bounds the blur from below, so lowering a valid one could
        # push it under what the signature allows.
        for blur in (0, 30, 44, 100):
            url = f"{self.WIXMP}/v1/fill/w_100,h_100,blur_{blur}/x.jpg?token=t"
            assert naming.clamp_wixmp_blur(url) == url

    def test_a_url_without_a_blur_is_untouched(self):
        url = f"{self.WIXMP}?token=t"
        assert naming.clamp_wixmp_blur(url) == url

    def test_only_wixmp_urls_are_rewritten(self):
        url = "https://example.com/v1/fill/blur_171/x.jpg"
        assert naming.clamp_wixmp_blur(url) == url
