"""The statistics accumulators and the detailed end-of-run summary."""

import json
from datetime import datetime, timezone

import pytest

from deviantart_downloader import sync
from deviantart_downloader import web as web_mod

from .conftest import blocked_web_item, make_dev, web_item


class TestHumanSize:
    def test_bytes_have_no_decimals(self):
        assert sync.human_size(0) == "0 B"
        assert sync.human_size(512) == "512 B"

    def test_scales_up_the_units(self):
        assert sync.human_size(1536) == "1.5 KB"
        assert sync.human_size(1024 * 1024) == "1.0 MB"
        assert sync.human_size(3 * 1024 ** 3) == "3.0 GB"

    def test_caps_at_terabytes(self):
        assert sync.human_size(5 * 1024 ** 4) == "5.0 TB"
        assert sync.human_size(4096 * 1024 ** 4).endswith("TB")


class TestFilterByContent:
    IMG = {"content": {"src": "x"}}
    LIT = {"type": "literature", "content": None}

    def test_none_keeps_everything(self):
        devs = [self.IMG, self.LIT]
        kept, dropped = sync.filter_by_content(devs, None)
        assert kept == devs and dropped == 0

    def test_images_only_drops_text(self):
        kept, dropped = sync.filter_by_content([self.IMG, self.LIT, self.IMG], "images")
        assert kept == [self.IMG, self.IMG] and dropped == 1

    def test_literature_only_drops_images(self):
        kept, dropped = sync.filter_by_content([self.IMG, self.LIT], "literature")
        assert kept == [self.LIT] and dropped == 1


class TestOnlySelectors:
    """Union within an axis, intersection across them."""

    IMG = {"content": {"src": "x"}}
    LIT = {"type": "literature", "content": None}
    MAT_IMG = {"content": {"src": "x"}, "is_mature": True}
    MAT_LIT = {"type": "literature", "content": None, "is_mature": True}
    ALL = [IMG, LIT, MAT_IMG, MAT_LIT]

    def keep(self, *selectors):
        kept, _ = sync.filter_by_content(self.ALL, frozenset(selectors))
        return kept

    def test_maturity_is_its_own_axis(self):
        assert self.keep("nsfw") == [self.MAT_IMG, self.MAT_LIT]

    def test_sfw_is_the_other_half_of_it(self):
        # Both routes report the flag, so this axis is symmetric: what is left
        # is what the listing said is not mature, not what it never mentioned.
        assert self.keep("sfw") == [self.IMG, self.LIT]

    def test_naming_both_maturities_is_the_same_as_naming_neither(self):
        assert self.keep("nsfw", "sfw") == self.ALL

    def test_a_kind_and_a_maturity_intersect(self):
        # The point of combining: the mature literature, not everything that is
        # either mature or literature.
        assert self.keep("literature", "nsfw") == [self.MAT_LIT]
        assert self.keep("images", "nsfw") == [self.MAT_IMG]
        assert self.keep("literature", "sfw") == [self.LIT]

    def test_naming_both_kinds_is_the_same_as_naming_neither(self):
        assert self.keep("images", "literature") == self.ALL

    def test_both_kinds_still_leave_the_other_axis_working(self):
        assert self.keep("images", "literature", "nsfw") == [self.MAT_IMG,
                                                             self.MAT_LIT]

    def test_a_work_the_listing_said_nothing_about_reads_as_sfw(self):
        # Worth pinning rather than leaving implicit: a work carrying no
        # maturity flag at all is kept by sfw and dropped by nsfw.
        silent = {"content": {"src": "x"}}
        kept, _ = sync.filter_by_content([silent], frozenset({"sfw"}))
        assert kept == [silent]
        kept, _ = sync.filter_by_content([silent], frozenset({"nsfw"}))
        assert kept == []

    def test_a_bare_string_is_not_read_as_its_letters(self):
        # frozenset("images") would be a set of characters and filter nothing.
        kept, _ = sync.filter_by_content(self.ALL, "literature")
        assert kept == [self.LIT, self.MAT_LIT]


class TestAxes:
    """The table the selectors come from, rather than each axis in turn."""

    def test_every_axis_value_is_a_selector(self):
        assert sync.ONLY_FILTERS == ("images", "literature", "nsfw", "sfw",
                                     "ai", "no-ai", "upscaled", "no-upscaled")

    def test_no_selector_belongs_to_two_axes(self):
        # A word on two axes would be filtered twice and read as neither.
        assert len(set(sync.ONLY_FILTERS)) == len(sync.ONLY_FILTERS)

    def test_only_the_website_only_axes_carry_a_warning(self):
        carried = {axis.values[0] for axis in sync.AXES if axis.unreported}
        assert carried == {"ai", "upscaled"}


class TestAiSelectors:
    """What the website's own "Suppress AI" filter reads."""

    AI = {"content": {"src": "x"}, "is_ai_generated": True}
    HUMAN = {"content": {"src": "x"}, "is_ai_generated": False}
    AI_LIT = {"type": "literature", "content": None, "is_ai_generated": True}
    UNKNOWN = {"content": {"src": "x"}, "is_ai_generated": None}   # API listing
    ALL = [AI, HUMAN, AI_LIT, UNKNOWN]

    def keep(self, *selectors):
        kept, _ = sync.filter_by_content(self.ALL, frozenset(selectors))
        return kept

    def test_ai_takes_only_the_works_known_to_be_ai_made(self):
        assert self.keep("ai") == [self.AI, self.AI_LIT]

    def test_no_ai_keeps_what_is_not_known_to_be_ai_made(self):
        # The unknown is kept: dropping it would act on a fact the listing
        # never carried.
        assert self.keep("no-ai") == [self.HUMAN, self.UNKNOWN]

    def test_naming_both_values_is_the_same_as_naming_neither(self):
        assert self.keep("ai", "no-ai") == self.ALL

    def test_the_ai_axis_intersects_with_the_kind(self):
        assert self.keep("ai", "images") == [self.AI]
        assert self.keep("ai", "literature") == [self.AI_LIT]

    def test_a_work_without_the_field_at_all_counts_as_unknown(self):
        kept, dropped = sync.filter_by_content([{"content": {"src": "x"}}],
                                               frozenset({"ai"}))
        assert kept == [] and dropped == 1


class TestUpscaledSelectors:
    """The fourth axis: works the author declared upscaled with AI."""

    UP = {"content": {"src": "x"}, "is_upscaled": True}
    PLAIN = {"content": {"src": "x"}, "is_upscaled": False}
    UP_LIT = {"type": "literature", "content": None, "is_upscaled": True}
    UNKNOWN = {"content": {"src": "x"}, "is_upscaled": None}        # API listing
    ALL = [UP, PLAIN, UP_LIT, UNKNOWN]

    def keep(self, *selectors):
        kept, _ = sync.filter_by_content(self.ALL, frozenset(selectors))
        return kept

    def test_upscaled_takes_only_the_works_known_to_be_upscaled(self):
        assert self.keep("upscaled") == [self.UP, self.UP_LIT]

    def test_no_upscaled_keeps_what_is_not_known_to_be(self):
        assert self.keep("no-upscaled") == [self.PLAIN, self.UNKNOWN]

    def test_naming_both_values_is_the_same_as_naming_neither(self):
        assert self.keep("upscaled", "no-upscaled") == self.ALL

    def test_it_is_its_own_axis_and_intersects_with_the_others(self):
        assert self.keep("upscaled", "images") == [self.UP]

    def test_an_upscaled_work_is_not_an_ai_generated_one(self):
        # Different declarations: a hand-drawn work upscaled with AI is not
        # AI-made, so the AI axis must not select it.
        drawn = {"content": {"src": "x"}, "is_upscaled": True,
                 "is_ai_generated": False}
        assert sync.filter_by_content([drawn], frozenset({"ai"}))[0] == []
        assert sync.filter_by_content([drawn], frozenset({"no-ai"}))[0] == [drawn]
        assert sync.filter_by_content([drawn], frozenset({"upscaled"}))[0] == [drawn]


class TestUnreportedWarnings:
    def test_the_website_listing_needs_no_warning(self):
        assert sync.unreported_warnings(frozenset({"ai", "upscaled"}), True) == []

    @pytest.mark.parametrize("selector", ["ai", "upscaled"])
    def test_an_api_listing_says_the_positive_matches_nothing(self, selector):
        warning, = sync.unreported_warnings(frozenset({selector}), False)
        assert f"--only {selector}" in warning and "matches no work" in warning

    @pytest.mark.parametrize("selector", ["no-ai", "no-upscaled"])
    def test_an_api_listing_says_the_negative_drops_nothing(self, selector):
        warning, = sync.unreported_warnings(frozenset({selector}), False)
        assert f"--only {selector}" in warning and "drops no work" in warning

    def test_each_axis_with_no_data_gets_its_own_line(self):
        warnings = sync.unreported_warnings(frozenset({"ai", "no-upscaled"}), False)
        assert len(warnings) == 2
        assert any("AI-generated" in w for w in warnings)
        assert any("upscaled with AI" in w for w in warnings)

    @pytest.mark.parametrize("only", [
        None,                                    # no --only at all
        frozenset({"nsfw"}),                     # an axis both routes report
        frozenset({"ai", "no-ai"}),              # both values: filters nothing
    ])
    def test_nothing_to_warn_about(self, only):
        assert sync.unreported_warnings(only, False) == []


class TestParseOnly:
    def test_repeated_words_and_commas_both_work(self):
        for given in (["literature", "nsfw"], ["literature,nsfw"],
                      ["literature, nsfw"], ["LITERATURE", "NSFW"]):
            assert sync.parse_only(given) == frozenset({"literature", "nsfw"})

    def test_nothing_selected_is_no_filter(self):
        assert sync.parse_only([""]) == frozenset()
        assert sync.parse_only(None) == frozenset()

    def test_the_ai_selectors_are_accepted(self):
        assert sync.parse_only(["no-ai,images"]) == frozenset({"no-ai", "images"})

    def test_an_unknown_selector_is_rejected_by_name(self):
        with pytest.raises(SystemExit, match="not: safe"):
            sync.parse_only(["images", "safe"])

    def test_a_profile_written_behind_only_explains_itself(self):
        """--only reads every word after it, so `--only images artist` lands here."""
        with pytest.raises(SystemExit, match="put it before --only"):
            sync.parse_only(["images", "artist"])


class TestParseDate:
    def test_a_bare_date_is_read_as_utc_midnight(self):
        assert sync.parse_date("2024-03-05", "--since") == datetime(
            2024, 3, 5, tzinfo=timezone.utc)

    def test_until_takes_a_bare_date_to_mean_the_whole_day(self):
        # Read as the instant the day begins, --until would silently drop
        # almost all of the day it names.
        assert sync.parse_date("2024-03-05", "--until", end_of_day=True) == datetime(
            2024, 3, 5, 23, 59, 59, 999999, tzinfo=timezone.utc)

    def test_a_spelled_out_time_is_taken_as_written(self):
        assert sync.parse_date("2024-03-05T00:00:00", "--until",
                               end_of_day=True) == datetime(
            2024, 3, 5, tzinfo=timezone.utc)

    def test_an_offset_is_honoured(self):
        assert sync.parse_date("2024-03-05T12:00:00+02:00", "--since") == datetime(
            2024, 3, 5, 10, tzinfo=timezone.utc)

    @pytest.mark.parametrize("value", ["ayer", "05/03/2024", "", "2024-13-01"])
    def test_a_value_that_is_not_a_date_exits_naming_the_flag(self, value):
        with pytest.raises(SystemExit, match="--since"):
            sync.parse_date(value, "--since")


class TestFilterByDate:
    OLD = {"published_time": "2023-01-01T00:00:00Z"}
    MID = {"published_time": "2024-06-01T00:00:00Z"}
    NEW = {"published_time": "2025-01-01T00:00:00Z"}
    UNDATED = {"title": "the listing carried no date"}

    def since(self):
        return sync.parse_date("2024-01-01", "--since")

    def until(self):
        return sync.parse_date("2024-12-31", "--until", end_of_day=True)

    def test_no_bounds_hands_back_the_same_list(self):
        works = [self.OLD, self.NEW]
        kept, dropped = sync.filter_by_date(works, None, None)
        assert kept is works and dropped == 0

    def test_since_keeps_the_bound_and_everything_after(self):
        kept, dropped = sync.filter_by_date(
            [self.OLD, self.MID, self.NEW], self.since(), None)
        assert kept == [self.MID, self.NEW] and dropped == 1

    def test_until_keeps_the_bound_and_everything_before(self):
        kept, dropped = sync.filter_by_date(
            [self.OLD, self.MID, self.NEW], None, self.until())
        assert kept == [self.OLD, self.MID] and dropped == 1

    def test_both_bounds_narrow_from_each_side(self):
        kept, dropped = sync.filter_by_date(
            [self.OLD, self.MID, self.NEW], self.since(), self.until())
        assert kept == [self.MID] and dropped == 2

    def test_the_bounds_are_inclusive(self):
        edge = {"published_time": "2024-01-01T00:00:00Z"}
        assert sync.filter_by_date([edge], self.since(), None) == ([edge], 0)

    def test_the_last_moment_of_an_until_day_is_still_inside(self):
        edge = {"published_time": "2024-12-31T23:59:59Z"}
        assert sync.filter_by_date([edge], None, self.until()) == ([edge], 0)

    def test_a_work_the_listing_gave_no_date_for_is_kept(self):
        """The lopsidedness --only has on the axes only the website reports.

        A bound narrows the run by what the listing said; it does not discard a
        work over a fact the listing never carried.
        """
        assert sync.filter_by_date(
            [self.UNDATED], self.since(), self.until()) == ([self.UNDATED], 0)

    def test_an_api_timestamp_is_compared_like_a_website_one(self):
        # 2024-06-01T00:00:00Z, as the API spells it.
        api_work = {"published_time": "1717200000"}
        kept, _ = sync.filter_by_date([api_work], self.since(), self.until())
        assert kept == [api_work]


class TestDateRangeLabel:
    def test_names_the_flags_that_set_the_bounds(self):
        since = sync.parse_date("2024-01-01", "--since")
        until = sync.parse_date("2024-12-31", "--until", end_of_day=True)
        assert sync.date_range_label(since, until) == (
            "--since 2024-01-01 --until 2024-12-31")
        assert sync.date_range_label(since, None) == "--since 2024-01-01"
        assert sync.date_range_label(None, until) == "--until 2024-12-31"
        assert sync.date_range_label(None, None) == ""


class TestInspectGallery:
    """Checking a gallery against its own record: read-only and offline."""

    def gallery(self, tmp_path, entries, files=(), partials=()):
        out = tmp_path / "artist"
        (out / "web").mkdir(parents=True)
        (out / "_downloaded.json").write_text(json.dumps(entries),
                                              encoding="utf-8")
        for name, content in files:
            (out / name).write_bytes(content)
        for name in partials:
            (out / name).write_bytes(b"")
        return tmp_path

    def test_a_folder_that_was_never_downloaded_has_nothing_to_say(self, tmp_path):
        assert sync.inspect_gallery(tmp_path, "nobody") is None

    def test_a_whole_gallery_reports_clean(self, tmp_path):
        root = self.gallery(tmp_path, {"111": "web/A_111.jpg"},
                            files=[("web/A_111.jpg", b"data")])
        damage = sync.inspect_gallery(root, "artist")
        assert damage.recorded == 1
        assert (damage.missing, damage.empty, damage.partial) == ([], [], [])

    def test_a_recorded_work_with_no_file_is_reported(self, tmp_path):
        root = self.gallery(tmp_path, {"111": "web/Gone_111.jpg"})
        assert sync.inspect_gallery(root, "artist").missing == ["web/Gone_111.jpg"]

    def test_an_empty_file_counts_as_damage_of_its_own(self, tmp_path):
        # What an interrupted write leaves, and what every other check would
        # happily call present.
        root = self.gallery(tmp_path, {"111": "web/Empty_111.jpg"},
                            files=[("web/Empty_111.jpg", b"")])
        damage = sync.inspect_gallery(root, "artist")
        assert damage.empty == ["web/Empty_111.jpg"] and damage.missing == []

    def test_leftover_part_files_are_found_in_either_subfolder(self, tmp_path):
        root = self.gallery(tmp_path, {}, partials=["web/Half_1.jpg.part",
                                                    "Old_2.jpg.part"])
        assert sync.inspect_gallery(root, "artist").partial == [
            "Old_2.jpg.part", "web/Half_1.jpg.part"]

    def test_it_writes_nothing_and_creates_nothing(self, tmp_path):
        root = self.gallery(tmp_path, {"111": "web/Gone_111.jpg"})
        before = {p: p.stat().st_mtime_ns for p in root.rglob("*")}
        sync.inspect_gallery(root, "artist")
        after = {p: p.stat().st_mtime_ns for p in root.rglob("*")}
        assert before == after


class TestVerificationLines:
    def test_a_clean_gallery_says_so_in_one_line(self):
        lines = sync.verification_lines("artist", sync.Damage(42, [], [], []))
        assert "42 recorded work(s), all present." in lines[1]

    def test_nothing_downloaded_is_not_damage(self):
        lines = sync.verification_lines("artist", None)
        assert "Nothing has been downloaded here." in lines[1]

    def test_each_kind_of_damage_names_itself_and_what_to_do(self):
        text = "\n".join(sync.verification_lines(
            "artist", sync.Damage(3, ["web/a.jpg"], ["web/b.jpg"],
                                  ["web/c.jpg.part"])))
        assert "1 on record but not on disk:" in text
        assert "--redownload-missing" in text
        assert "1 empty file(s):" in text
        assert "1 leftover .part file(s)" in text

    def test_a_long_list_is_capped_rather_than_dumped(self):
        many = [f"web/{i}.jpg" for i in range(25)]
        text = "\n".join(sync.verification_lines("artist",
                                                 sync.Damage(25, many, [], [])))
        assert "... and 15 more" in text
        assert "web/24.jpg" not in text


class TestVerifyUsers:
    def test_it_answers_false_when_anything_is_wrong(self, tmp_path, capsys):
        out = tmp_path / "artist"
        (out / "web").mkdir(parents=True)
        (out / "_downloaded.json").write_text('{"111": "web/Gone_111.jpg"}',
                                              encoding="utf-8")
        assert sync.verify_users(tmp_path, ["artist"]) is False
        assert "not on disk" in capsys.readouterr().out

    def test_it_answers_true_when_there_is_nothing_to_report(self, tmp_path):
        (tmp_path / "artist").mkdir()
        assert sync.verify_users(tmp_path, ["artist"]) is True


class TestPlanSummary:
    def recorded_gallery(self, tmp_path, on_disk=True):
        """A gallery whose record holds the plain web_item, file optional."""
        from deviantart_downloader.manifest import DownloadManifest
        (tmp_path / "web").mkdir(parents=True, exist_ok=True)
        if on_disk:
            (tmp_path / "web" / "Web Art_1004952679.jpg").write_bytes(b"data")
        manifest = DownloadManifest(tmp_path)
        manifest.add("1004952679", "web/Web Art_1004952679.jpg")
        return manifest

    def test_it_splits_the_routes_and_counts_what_is_already_here(self, tmp_path):
        manifest = self.recorded_gallery(tmp_path)
        normalised = [web_mod.normalize_web_deviation(d)
                      for d in (web_item(), blocked_web_item())]
        text = "\n".join(sync.plan_summary(normalised, manifest, True, tmp_path))
        assert "nothing is downloaded, and nothing is written" in text
        assert "2 work(s) selected: 1 via the website (web/), 1 via the API" in text
        assert "1 would be skipped, so 1 would be fetched." in text

    def test_it_says_the_api_lookup_was_not_spent(self, tmp_path):
        normalised = [web_mod.normalize_web_deviation(blocked_web_item())]
        text = "\n".join(sync.plan_summary(normalised, None, True, tmp_path))
        assert "would need a listing lookup on the API first" in text

    def test_an_api_listing_has_no_lookup_left_to_mention(self, tmp_path):
        text = "\n".join(sync.plan_summary([make_dev()], None, False, tmp_path))
        assert "listing lookup" not in text

    def test_no_manifest_means_nothing_would_be_skipped(self, tmp_path):
        normalised = [web_mod.normalize_web_deviation(web_item())]
        text = "\n".join(sync.plan_summary(normalised, None, True, tmp_path))
        assert "0 would be skipped, so 1 would be fetched." in text

    def test_a_recorded_work_whose_file_is_gone_is_still_skipped_by_default(
            self, tmp_path):
        # The record is authoritative: a file you deleted stays deleted.
        manifest = self.recorded_gallery(tmp_path, on_disk=False)
        normalised = [web_mod.normalize_web_deviation(web_item())]
        text = "\n".join(sync.plan_summary(normalised, manifest, True, tmp_path))
        assert "1 would be skipped, so 0 would be fetched." in text

    def test_redownload_missing_counts_the_deleted_ones_as_fetched(self, tmp_path):
        """Being on record is not enough on its own -- this flag exists to
        fetch back exactly the works whose file has gone."""
        manifest = self.recorded_gallery(tmp_path, on_disk=False)
        normalised = [web_mod.normalize_web_deviation(web_item())]
        text = "\n".join(sync.plan_summary(normalised, manifest, True, tmp_path,
                                           redownload_missing=True))
        assert "0 would be skipped, so 1 would be fetched." in text

    def test_redownload_blurred_counts_the_api_route_copies_as_fetched(
            self, tmp_path):
        from deviantart_downloader.manifest import DownloadManifest
        (tmp_path / "api").mkdir(parents=True)
        (tmp_path / "api" / "Mature Art_222222222.jpg").write_bytes(b"data")
        manifest = DownloadManifest(tmp_path)
        manifest.add("222222222", "api/Mature Art_222222222.jpg")
        normalised = [web_mod.normalize_web_deviation(blocked_web_item())]
        plain = "\n".join(sync.plan_summary(normalised, manifest, True, tmp_path))
        repair = "\n".join(sync.plan_summary(normalised, manifest, True, tmp_path,
                                             redownload_blurred=True))
        assert "1 would be skipped, so 0 would be fetched." in plain
        assert "0 would be skipped, so 1 would be fetched." in repair


class TestAddStats:
    def test_folds_routes_and_totals(self):
        totals = sync.new_stats()
        a = sync.new_stats()
        a["downloaded"] = 2
        a["bytes"] = 30
        a["elapsed"] = 1.0
        a["web"] = {"downloaded": 2, "bytes": 30}
        b = sync.new_stats()
        b["downloaded"] = 1
        b["skipped"] = 4
        b["bytes"] = 10
        b["elapsed"] = 0.5
        b["api"] = {"downloaded": 1, "bytes": 10}

        sync.add_stats(totals, a)
        sync.add_stats(totals, b)

        assert totals["downloaded"] == 3
        assert totals["skipped"] == 4
        assert totals["bytes"] == 40
        assert totals["elapsed"] == 1.5
        assert totals["web"] == {"downloaded": 2, "bytes": 30}
        assert totals["api"] == {"downloaded": 1, "bytes": 10}


class TestSummaryLines:
    def test_header_keeps_the_compact_shape(self):
        stats = sync.new_stats()
        stats["skipped"] = 5
        stats["no_media"] = 1
        lines = sync.summary_lines(stats)
        assert lines == [
            "Downloaded: 0 | Skipped (already existed): 5 | No file: 1 "
            "| Failed: 0 | API requests: 0"
        ]

    def test_the_api_requests_are_always_counted(self):
        # Zero included: a re-sync that spent nothing is the website route
        # working, which is worth seeing rather than inferring.
        stats = sync.new_stats()
        assert "API requests: 0" in sync.summary_lines(stats)[0]
        stats["requests"] = 7
        assert "API requests: 7" in sync.summary_lines(stats)[0]

    def test_the_request_count_is_not_accumulated(self):
        """It is read off the client, not summed: a user skipped whole returns
        no stats to add up, but the requests that found that out were spent."""
        totals, one = sync.new_stats(), sync.new_stats()
        one["requests"] = 4
        sync.add_stats(totals, one)
        assert totals["requests"] == 0

    def test_breaks_downloads_down_by_route(self):
        stats = sync.new_stats()
        stats["downloaded"] = 3
        stats["bytes"] = 3 * 1024 * 1024
        stats["elapsed"] = 2.0
        stats["web"] = {"downloaded": 2, "bytes": 2 * 1024 * 1024}
        stats["api"] = {"downloaded": 1, "bytes": 1024 * 1024}

        lines = sync.summary_lines(stats, users=2)
        body = "\n".join(lines)
        assert "via website: 2 item(s), 2.0 MB" in body
        assert "via API:     1 item(s), 1.0 MB" in body
        assert "Total downloaded: 3.0 MB" in body
        assert "in 2.0s (1.5 MB/s)" in body
        assert "avg 1.0 MB/file" in body
        assert "across 2 user(s)" in body

    def test_cancelled_shown_only_when_present(self):
        stats = sync.new_stats()
        stats["cancelled"] = 2
        assert "Cancelled: 2" in sync.summary_lines(stats)[0]
        assert "Cancelled" not in sync.summary_lines(sync.new_stats())[0]


class TestSummaryReportsReplacements:
    """After a repair pass the number worth knowing is how many actually changed."""

    def stats(self, **counts):
        s = sync.new_stats()
        s.update(counts)
        s["api"] = {"downloaded": counts.get("downloaded", 0) + counts.get("replaced", 0),
                    "bytes": s["bytes"]}
        return s

    def test_replacements_are_counted_apart_from_new_works(self):
        line = sync.summary_lines(self.stats(downloaded=1, replaced=3, bytes=400))[0]
        assert "Downloaded: 1" in line
        assert "Replaced (were blurred): 3" in line

    def test_a_run_that_replaced_nothing_says_nothing_about_it(self):
        line = sync.summary_lines(self.stats(downloaded=2, bytes=200))[0]
        assert "Replaced" not in line

    def test_replacing_without_downloading_still_gets_a_breakdown(self):
        """A repair pass usually downloads nothing new at all."""
        lines = sync.summary_lines(self.stats(replaced=4, bytes=400))
        assert len(lines) > 1                      # the per-route breakdown ran
        assert "avg 100 B/file" in lines[-1]       # averaged over the 4 replaced
