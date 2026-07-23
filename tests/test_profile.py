"""Inspecting a profile: gathering its facts, stats and galleries."""

import pytest

from deviantart_downloader import profile
from deviantart_downloader.web import WebError

from .conftest import FakeClient, FakeWebClient


def web_about(**about_overrides):
    """A website 'about' response with its two useful modules."""
    about = {
        "country": "Canada", "countryId": 2,
        "age": 35, "dobYear": 1991, "dobMonth": 6, "dobDay": 18,
        "deviantFor": 644_336_040,  # ~20 years, in seconds
        "isArtist": True, "website": "patreon.com/artist",
        "websiteLabel": "patreon", "twitterUsername": "artist_tw",
        "gender": None, "tagline": "draws things",
        "badges": [{"title": "Diamond"}, {"title": "Emerald"}],
    }
    about.update(about_overrides)
    userstats = {"deviations": 2017, "watchers": 676104, "watching": 258,
                 "pageviews": 54305859, "favourites": 3172,
                 "commentsReceivedProfile": 31211, "commentsMade": 12464}
    return {"gruser": {"page": {"modules": [
        {"name": "about", "moduleData": {"about": about}},
        {"name": "userstats", "moduleData": {"userstats": userstats}},
    ]}}}


def api_profile(**overrides):
    data = {
        "user": {"username": "artist"},
        "profile_url": "https://www.deviantart.com/artist",
        "user_is_artist": True, "artist_specialty": "Digital Art",
        "real_name": "Jane Doe", "tagline": "", "country": "Canada",
        "website": "", "bio": "Hi <b>there</b><br>second line",
        "stats": {"user_deviations": 2017, "user_favourites": 3172,
                  "user_comments": 12464, "profile_pageviews": 54305859,
                  "profile_comments": 31211},
    }
    data.update(overrides)
    return data


class TestExtraction:
    def test_from_web_about_pulls_the_rich_header(self):
        out = profile._from_web_about(web_about())
        assert out["country"] == "Canada"
        assert out["birthday"] == "18 June 1991"
        assert out["deviant_for_years"] == 20
        assert out["badges"] == ["Diamond", "Emerald"]
        assert out["stats"]["watchers"] == 676104
        assert out["stats"]["comments_received"] == 31211

    def test_from_api_profile_pulls_bio_and_real_name(self):
        out = profile._from_api_profile(api_profile())
        assert out["real_name"] == "Jane Doe"
        assert out["bio"] == "Hi there\nsecond line"   # tags stripped, <br> kept
        assert out["specialty"] == "Digital Art"
        assert out["stats"]["deviations"] == 2017

    @pytest.mark.parametrize("about,expected", [
        ({"dobYear": 1991, "dobMonth": 6, "dobDay": 18}, "18 June 1991"),
        ({"dobYear": None, "dobMonth": 6, "dobDay": 18}, "18 June"),   # year hidden
        ({"dobMonth": None, "dobDay": None}, None),                    # not shared
    ])
    def test_birthday_handles_missing_parts(self, about, expected):
        assert profile._birthday(about) == expected

    def test_years_and_plain_text_helpers(self):
        assert profile._years(644_336_040) == 20
        assert profile._years(None) is None
        assert profile._plain_text("<p>a</p><br>b") == "a\nb"
        assert profile._plain_text("") is None


class TestGatherProfile:
    def test_web_supplies_the_header_api_fills_the_bio(self):
        web = FakeWebClient(about=web_about(),
                            folders=[{"name": "Featured", "size": 1373}])
        client = FakeClient(pages=[api_profile()])
        info = profile.gather_profile(client, web, "artist")
        # Web wins for stats it also carries...
        assert info["stats"]["watchers"] == 676104
        # ...and the API fills what the website omits.
        assert info["bio"] == "Hi there\nsecond line"
        assert info["real_name"] == "Jane Doe"
        assert info["galleries"] == [{"name": "Featured", "size": 1373}]
        assert len(client.calls) == 1   # only user/profile, folders came from web

    def test_falls_back_to_the_api_when_the_website_breaks(self, capsys):
        class BrokenWeb(FakeWebClient):
            def profile_about(self, username):
                raise WebError("profile module moved")

        client = FakeClient(pages=[
            api_profile(),
            {"results": [{"name": "Featured", "size": 40}], "has_more": False},
        ])
        info = profile.gather_profile(client, BrokenWeb(), "artist")
        assert info["stats"]["deviations"] == 2017      # from the API
        assert info["galleries"] == [{"name": "Featured", "size": 40}]
        assert "falling back to the API" in capsys.readouterr().out


class TestFormatProfile:
    def test_renders_the_present_fields(self):
        web = FakeWebClient(about=web_about(),
                            folders=[{"name": "Featured", "size": 1373},
                                     {"name": "Sketches", "size": None}])
        client = FakeClient(pages=[api_profile()])
        text = profile.format_profile(profile.gather_profile(client, web, "artist"))
        assert "Profile: artist (Jane Doe)" in text
        assert "Birthday: 18 June 1991 (age 35)" in text
        assert "Deviant for: 20 years" in text
        assert "Watchers: 676,104" in text
        assert "Links: patreon.com/artist (patreon), twitter: @artist_tw" in text
        assert "Galleries: 2 folder(s), 1,373 items" in text
        assert "- Featured — 1,373 items" in text
        assert "- Sketches" in text and "Sketches —" not in text  # unknown size

    def test_omits_absent_sections(self):
        text = profile.format_profile(
            {"username": "ghost", "profile_url": "u", "galleries": []})
        assert text.startswith("Profile: ghost")
        assert "Birthday" not in text and "Statistics" not in text
        assert "Galleries: 0 folder(s)" in text
