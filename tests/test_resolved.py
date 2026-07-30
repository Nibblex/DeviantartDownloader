"""The cache of what the API answered about the works that need it."""

import json

from deviantart_downloader import resolved as resolved_mod
from deviantart_downloader.resolved import FILENAME, ResolvedCache

from .conftest import BASE_URI, make_dev

CLEAN = f"{BASE_URI}?token=tok"
BLURRED = "https://images-wixmp-abc.wixmp.com/f/u/x.jpg/v1/fill/w_300,blur_60/x.jpg"


def entry(src=CLEAN, **kw):
    return make_dev(content={"src": src}, **kw)


def written(tmp_path):
    return json.loads((tmp_path / FILENAME).read_text(encoding="utf-8"))


class TestRememberAndGet:
    def test_an_empty_cache_answers_nothing(self, tmp_path):
        assert ResolvedCache(tmp_path).get("222", user_mode=False) is None

    def test_what_was_remembered_comes_back(self, tmp_path):
        cache = ResolvedCache(tmp_path)
        cache.remember({"222": entry()})
        assert cache.get("222", user_mode=False) == entry()

    def test_it_outlives_the_run_that_paid_for_it(self, tmp_path):
        ResolvedCache(tmp_path).remember({"222": entry()})
        assert ResolvedCache(tmp_path).get("222", user_mode=False) == entry()
        assert list(written(tmp_path)) == ["222"]

    def test_a_work_it_never_saw_is_not_invented(self, tmp_path):
        cache = ResolvedCache(tmp_path)
        cache.remember({"222": entry()})
        assert cache.get("333", user_mode=False) is None

    def test_remembering_nothing_writes_nothing(self, tmp_path):
        ResolvedCache(tmp_path).remember({})
        assert not (tmp_path / FILENAME).exists()

    def test_a_work_with_no_key_is_not_recorded(self, tmp_path):
        # Nothing could ever look it up again, and "" would collide.
        ResolvedCache(tmp_path).remember({"": entry()})
        assert not (tmp_path / FILENAME).exists()


class TestTheBlurRule:
    """A logged-out run caches the blur; a session can do better than that."""

    def test_a_cached_blur_is_not_reused_once_logged_in(self, tmp_path):
        cache = ResolvedCache(tmp_path)
        cache.remember({"222": entry(src=BLURRED)})
        assert cache.get("222", user_mode=True) is None

    def test_a_logged_out_run_still_takes_the_blur(self, tmp_path):
        # It is what that run would have fetched anyway.
        cache = ResolvedCache(tmp_path)
        cache.remember({"222": entry(src=BLURRED)})
        assert cache.get("222", user_mode=False) is not None

    def test_an_unblurred_answer_serves_either_run(self, tmp_path):
        # The CDN token authorises the fetch, not the session that got the URL.
        cache = ResolvedCache(tmp_path)
        cache.remember({"222": entry()})
        assert cache.get("222", user_mode=True) == entry()
        assert cache.get("222", user_mode=False) == entry()

    def test_an_answer_with_no_url_is_never_taken_for_a_blur(self, tmp_path):
        cache = ResolvedCache(tmp_path)
        cache.remember({"222": make_dev(content=None)})
        assert cache.get("222", user_mode=True) is not None


class TestForget:
    def test_a_dropped_answer_is_gone_from_the_file_too(self, tmp_path):
        cache = ResolvedCache(tmp_path)
        cache.remember({"222": entry(), "333": entry(deviationid="other")})
        cache.forget("222")
        assert cache.get("222", user_mode=False) is None
        assert list(written(tmp_path)) == ["333"]

    def test_forgetting_what_was_never_there_writes_nothing(self, tmp_path):
        cache = ResolvedCache(tmp_path)
        cache.remember({"222": entry()})
        before = (tmp_path / FILENAME).stat().st_mtime_ns
        cache.forget("333")
        assert (tmp_path / FILENAME).stat().st_mtime_ns == before


class TestDamagedFile:
    def test_unreadable_json_starts_empty(self, tmp_path, capsys):
        (tmp_path / FILENAME).write_text("{not json", encoding="utf-8")
        assert ResolvedCache(tmp_path).get("222", user_mode=False) is None
        # It is a cache: regenerating it is what read_json's warning promises.
        assert "WARNING" in capsys.readouterr().out

    def test_entries_that_are_not_answers_are_ignored(self, tmp_path):
        (tmp_path / FILENAME).write_text(json.dumps({"222": "nope", "333": {"a": 1}}),
                                         encoding="utf-8")
        cache = ResolvedCache(tmp_path)
        assert cache.get("222", user_mode=False) is None
        assert cache.get("333", user_mode=False) == {"a": 1}

    def test_a_cache_that_cannot_be_written_does_not_end_the_run(self, tmp_path,
                                                                monkeypatch):
        cache = ResolvedCache(tmp_path)
        monkeypatch.setattr(resolved_mod, "write_json",
                            lambda *a: (_ for _ in ()).throw(OSError("read-only")))
        cache.remember({"222": entry()})          # no exception
        assert cache.get("222", user_mode=False) == entry()
