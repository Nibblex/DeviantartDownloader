"""Inspecting a profile: gathering its facts, stats and galleries."""

import json
import time

import pytest
import requests

from deviantart_downloader import api, profile
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
        # The bio, in the current editor's format, exactly as a text work carries it.
        "textContent": {"html": {"type": "tiptap", "markup": json.dumps({
            "document": {"type": "doc", "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "Hi there"}]},
                {"type": "paragraph", "content": [{"type": "text", "text": "second line"}]},
            ]}})}},
    }
    about.update(about_overrides)
    userstats = {"deviations": 2017, "watchers": 676104, "watching": 258,
                 "pageviews": 54305859, "favourites": 3172,
                 "commentsReceivedProfile": 31211, "commentsMade": 12464}
    # The cover module keys its payload by "coverDeviation", not by its own name.
    cover = {"coverDeviation": {"media": {
        "baseUri": "https://images.example/banner.jpg",
        "prettyName": "banner_by_artist",
        "token": ["tok"],
        "types": [{"t": "fullview", "r": 0}],
    }}}
    return {"owner": {"usericon": "https://a.deviantart.net/avatars-big/a/r/artist.jpg"},
            "gruser": {"page": {"modules": [
                {"name": "about", "moduleData": {"about": about}},
                {"name": "userstats", "moduleData": {"userstats": userstats}},
                {"name": "cover_deviation", "moduleData": {"coverDeviation": cover}},
            ]}}}


def api_profile(**overrides):
    data = {
        "user": {"username": "artist",
                 "usericon": "https://a.deviantart.net/avatars/a/r/artist.jpg"},
        "cover_photo": "",
        "cover_deviation": {"cover_deviation": {
            "content": {"src": "https://images.example/api-banner.jpg?token=t"}}},
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
        assert out["avatar"].endswith("avatars-big/a/r/artist.jpg")
        assert out["banner"] == "https://images.example/banner.jpg?token=tok"

    def test_from_web_about_survives_a_profile_with_no_banner(self):
        about = web_about()
        modules = about["gruser"]["page"]["modules"]
        # What the website sends for a profile that never set a cover.
        modules[-1]["moduleData"] = {"coverDeviation": {"coverDeviation": None}}
        assert profile._from_web_about(about)["banner"] is None

    def test_from_api_profile_pulls_bio_and_real_name(self):
        out = profile._from_api_profile(api_profile())
        assert out["real_name"] == "Jane Doe"
        assert out["bio"] == "Hi there\nsecond line"   # tags stripped, <br> kept
        assert out["specialty"] == "Digital Art"
        assert out["stats"]["deviations"] == 2017
        assert out["avatar"].endswith("avatars/a/r/artist.jpg")
        assert out["banner"] == "https://images.example/api-banner.jpg?token=t"

    def test_from_api_profile_falls_back_to_the_preview_cover(self):
        out = profile._from_api_profile(api_profile(cover_deviation={
            "cover_deviation": {"preview": {"src": "https://images.example/p.jpg"}}}))
        assert out["banner"] == "https://images.example/p.jpg"

    def test_from_api_profile_without_a_cover(self):
        assert profile._from_api_profile(
            api_profile(cover_deviation=None))["banner"] is None

    @pytest.mark.parametrize("about,expected", [
        ({"dobYear": 1991, "dobMonth": 6, "dobDay": 18}, "18 June 1991"),
        ({"dobYear": None, "dobMonth": 6, "dobDay": 18}, "18 June"),   # year hidden
        ({"dobMonth": None, "dobDay": None}, None),                    # not shared
    ])
    def test_birthday_handles_missing_parts(self, about, expected):
        assert profile._birthday(about) == expected

    def test_years_helper(self):
        assert profile._years(644_336_040) == 20
        assert profile._years(None) is None

    def test_the_api_bio_is_rendered_not_just_stripped(self):
        # The old hand-rolled stripper left entities raw and ran blocks together.
        assert profile._api_bio("<p>a &amp; b</p><p>c</p>") == "a & b\nc"
        assert profile._api_bio("") is None


class TestGatherProfile:
    def test_the_website_answers_alone_and_spends_no_api_quota(self):
        web = FakeWebClient(about=web_about(),
                            folders=[{"name": "Featured", "size": 1373}])
        client = FakeClient(pages=[api_profile()])
        info = profile.gather_profile(client, web, "artist")
        assert info["stats"]["watchers"] == 676104
        assert info["bio"] == "Hi there\nsecond line"    # rendered from tiptap
        assert info["galleries"] == [{"name": "Featured", "size": 1373}]
        # The website's avatar and banner come at a higher resolution.
        assert info["avatar"].endswith("avatars-big/a/r/artist.jpg")
        assert info["banner"] == "https://images.example/banner.jpg?token=tok"
        # The point of the exercise: one profile costs zero API requests, so a
        # --watching --info over a long watchlist costs zero too.
        assert client.calls == []

    def test_the_website_route_gives_up_the_api_only_fields(self):
        web = FakeWebClient(about=web_about(),
                            folders=[{"name": "Featured", "size": 1373}])
        info = profile.gather_profile(FakeClient(pages=[api_profile()]), web, "artist")
        # DeviantArt publishes neither on the website; --force-api gets them back.
        assert not info.get("real_name")
        assert not info.get("specialty")

    def test_force_api_restores_the_full_profile(self):
        # --force-api leaves web None, which is what routes this through the API.
        client = FakeClient(pages=[
            api_profile(),
            {"results": [{"name": "Featured", "size": 40}], "has_more": False},
        ])
        info = profile.gather_profile(client, None, "artist")
        assert info["real_name"] == "Jane Doe"
        assert info["specialty"] == "Digital Art"
        assert info["bio"] == "Hi there\nsecond line"
        assert [endpoint for endpoint, _ in client.calls][0] == "user/profile/artist"

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
        assert info["avatar"].endswith("avatars/a/r/artist.jpg")
        assert info["banner"] == "https://images.example/api-banner.jpg?token=t"
        assert info["galleries"] == [{"name": "Featured", "size": 40}]
        assert "falling back to the API" in capsys.readouterr().out


class TestFormatProfile:
    def test_renders_the_present_fields(self):
        web = FakeWebClient(about=web_about(),
                            folders=[{"name": "Featured", "size": 1373},
                                     {"name": "Sketches", "size": None}])
        client = FakeClient(pages=[api_profile()])
        text = profile.format_profile(profile.gather_profile(client, web, "artist"))
        # No real name: the website route does not carry one (see --force-api).
        assert "Profile: artist — https://www.deviantart.com/artist" in text
        assert "Avatar: https://a.deviantart.net/avatars-big/a/r/artist.jpg" in text
        assert "Banner: https://images.example/banner.jpg?token=tok" in text
        assert "Birthday: 18 June 1991 (age 35)" in text
        assert "Deviant for: 20 years" in text
        assert "Watchers: 676,104" in text
        assert "Links: patreon.com/artist (patreon), twitter: @artist_tw" in text
        assert "Galleries: 2 folder(s), 1,373 items" in text
        assert "- Featured — 1,373 items" in text
        assert "- Sketches" in text and "Sketches —" not in text  # unknown size

    def test_omits_absent_sections(self):
        text = profile.format_profile(
            {"username": "ghost", "galleries": []})
        assert text.startswith("Profile: ghost — https://www.deviantart.com/ghost")
        assert "Avatar" not in text and "Banner" not in text
        assert "Birthday" not in text and "Statistics" not in text
        assert "Galleries: 0 folder(s)" in text


class TestPrintProfiles:
    """The fan-out --info uses: fetched concurrently, printed in order."""

    def fake_gather(self, monkeypatch, delays=None, gone=()):
        """gather_profile that can be slow per user and can report a dead one."""
        delays = delays or {}

        def gather(client, web, username):
            if username in gone:
                raise api.UserNotFoundError(f'User "{username}" not found.')
            time.sleep(delays.get(username, 0))
            return {"username": username, "galleries": []}

        monkeypatch.setattr(profile, "gather_profile", gather)

    def printed_order(self, capsys):
        return [line.split()[1] for line in capsys.readouterr().out.splitlines()
                if line.startswith("Profile:")]

    def test_output_follows_the_input_order_not_the_finishing_order(
            self, monkeypatch, capsys):
        names = ["alice", "bob", "carol"]
        # alice is the slowest, so unordered output would put her last.
        self.fake_gather(monkeypatch, delays={"alice": 0.15, "bob": 0.05})
        profile.print_profiles(None, None, names, workers=3)
        assert self.printed_order(capsys) == names

    def test_the_users_are_fetched_concurrently(self, monkeypatch, capsys):
        names = [f"user{i}" for i in range(4)]
        self.fake_gather(monkeypatch, delays=dict.fromkeys(names, 0.1))
        started = time.monotonic()
        profile.print_profiles(None, None, names, workers=4)
        # Serial would be 4 x 0.1s; concurrent is one of them plus overhead.
        assert time.monotonic() - started < 0.25
        assert self.printed_order(capsys) == names

    def test_one_worker_still_works(self, monkeypatch, capsys):
        self.fake_gather(monkeypatch)
        profile.print_profiles(None, None, ["alice", "bob"], workers=1)
        assert self.printed_order(capsys) == ["alice", "bob"]

    def test_a_gone_account_is_skipped_and_the_rest_still_print(
            self, monkeypatch, capsys):
        self.fake_gather(monkeypatch, gone={"ghost"})
        profile.print_profiles(None, None, ["ghost", "alice"], workers=2,
                               skip_missing=True)
        out = capsys.readouterr().out
        assert 'User "ghost" not found.' in out and "Skipping ghost." in out
        assert "Profile: alice" in out

    def test_a_profile_blocked_to_us_is_skipped_like_a_gone_one(
            self, monkeypatch, capsys):
        """DeviantArt closes some profiles; that is not the run's problem."""
        def gather(client, web, username):
            if username == "blocked":
                raise requests.HTTPError("400 Client Error: Bad Request")
            return {"username": username, "galleries": []}

        monkeypatch.setattr(profile, "gather_profile", gather)
        profile.print_profiles(None, None, ["blocked", "alice"], workers=2,
                               skip_missing=True)
        out = capsys.readouterr().out
        assert "Skipping blocked." in out and "Profile: alice" in out

    def test_a_named_profile_that_is_gone_still_fails_loudly(self, monkeypatch):
        self.fake_gather(monkeypatch, gone={"ghost"})
        with pytest.raises(api.UserNotFoundError):
            profile.print_profiles(None, None, ["ghost"])
