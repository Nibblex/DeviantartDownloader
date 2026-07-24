"""The statistics accumulators and the detailed end-of-run summary."""

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
