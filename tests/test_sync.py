"""The statistics accumulators and the detailed end-of-run summary."""

import pytest

from deviantart_downloader import sync


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

    def test_mature_is_its_own_axis(self):
        assert self.keep("mature") == [self.MAT_IMG, self.MAT_LIT]

    def test_a_kind_and_mature_intersect(self):
        # The point of combining: the mature literature, not everything that is
        # either mature or literature.
        assert self.keep("literature", "mature") == [self.MAT_LIT]
        assert self.keep("images", "mature") == [self.MAT_IMG]

    def test_naming_both_kinds_is_the_same_as_naming_neither(self):
        assert self.keep("images", "literature") == self.ALL

    def test_both_kinds_still_leave_the_other_axis_working(self):
        assert self.keep("images", "literature", "mature") == [self.MAT_IMG,
                                                               self.MAT_LIT]

    def test_a_bare_string_is_not_read_as_its_letters(self):
        # frozenset("images") would be a set of characters and filter nothing.
        kept, _ = sync.filter_by_content(self.ALL, "literature")
        assert kept == [self.LIT, self.MAT_LIT]


class TestAiSelectors:
    """The third axis: what the website's own "Suppress AI" filter reads."""

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


class TestAiAxisWarning:
    def test_the_website_listing_needs_no_warning(self):
        assert sync.ai_axis_warning(frozenset({"ai"}), True) is None

    def test_an_api_listing_says_ai_matches_nothing(self):
        warning = sync.ai_axis_warning(frozenset({"ai"}), False)
        assert "--only ai" in warning and "matches no work" in warning

    def test_an_api_listing_says_no_ai_drops_nothing(self):
        warning = sync.ai_axis_warning(frozenset({"no-ai"}), False)
        assert "--only no-ai" in warning and "drops no work" in warning

    @pytest.mark.parametrize("only", [
        None,                             # no --only at all
        frozenset({"mature"}),            # another axis entirely
        frozenset({"ai", "no-ai"}),       # both values: filters nothing anyway
    ])
    def test_nothing_to_warn_about(self, only):
        assert sync.ai_axis_warning(only, False) is None


class TestParseOnly:
    def test_repeated_words_and_commas_both_work(self):
        for given in (["literature", "mature"], ["literature,mature"],
                      ["literature, mature"], ["LITERATURE", "Mature"]):
            assert sync.parse_only(given) == frozenset({"literature", "mature"})

    def test_nothing_selected_is_no_filter(self):
        assert sync.parse_only([""]) == frozenset()
        assert sync.parse_only(None) == frozenset()

    def test_the_ai_selectors_are_accepted(self):
        assert sync.parse_only(["no-ai,images"]) == frozenset({"no-ai", "images"})

    def test_an_unknown_selector_is_rejected_by_name(self):
        with pytest.raises(SystemExit, match="not: sfw"):
            sync.parse_only(["images", "sfw"])

    def test_a_profile_written_behind_only_explains_itself(self):
        """--only reads every word after it, so `--only images artist` lands here."""
        with pytest.raises(SystemExit, match="put it before --only"):
            sync.parse_only(["images", "artist"])


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
            "Downloaded: 0 | Skipped (already existed): 5 | No file: 1 | Failed: 0"
        ]

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
