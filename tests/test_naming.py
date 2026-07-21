"""Usernames, file names and the deviation key shared by both routes."""

import pytest

from deviantart_downloader import web as web_mod
from deviantart_downloader import naming

from .conftest import WEB_ID, WEB_URL, DEV_ID, make_dev, web_item


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
