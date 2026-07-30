"""The per-user settings file: what it sets, and surviving a rename."""

import json

import pytest

from deviantart_downloader import overrides as ov
from deviantart_downloader.constants import WEB_SUBDIR

from .conftest import API_USER_ID, WEB_USER_ID, make_dev

WEB_WORK = {"_source": WEB_SUBDIR,
            "author": {"userid": WEB_USER_ID, "username": "artist"}}


def write_file(tmp_path, entries):
    path = tmp_path / ov.FILENAME
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


def load(tmp_path, entries):
    return ov.UserOverrides(write_file(tmp_path, entries))


def read_back(tmp_path):
    return json.loads((tmp_path / ov.FILENAME).read_text(encoding="utf-8"))


class TestUserIds:
    def test_a_website_listing_reports_the_numeric_id(self):
        assert ov.user_ids([WEB_WORK]) == {"web": str(WEB_USER_ID)}

    def test_an_api_listing_reports_the_uuid(self):
        assert ov.user_ids([make_dev()]) == {"api": API_USER_ID}

    def test_a_listing_without_an_author_reports_nothing(self):
        assert ov.user_ids([{"_source": WEB_SUBDIR}, {}]) == {}

    def test_both_routes_are_kept_apart(self):
        # A run that used both routes learns one id per route, not one id.
        assert ov.user_ids([WEB_WORK, make_dev()]) == {
            "web": str(WEB_USER_ID), "api": API_USER_ID}


class TestNoSettings:
    def test_a_missing_file_changes_nothing(self, tmp_path):
        conf = ov.UserOverrides(tmp_path / ov.FILENAME)
        assert conf.for_user("artist", [WEB_WORK], frozenset({"images"}), "txt") == (
            frozenset({"images"}), "txt")

    def test_a_user_the_file_does_not_name_keeps_the_command_line(self, tmp_path):
        conf = load(tmp_path, {"someone-else": {"only": "literature"}})
        assert conf.for_user("artist", [WEB_WORK], None, "txt") == (None, "txt")


class TestSettingsApply:
    def test_only_is_replaced_for_that_user(self, tmp_path):
        conf = load(tmp_path, {"artist": {"only": "literature, mature"}})
        only, fmt = conf.for_user("artist", [WEB_WORK], frozenset({"images"}), "txt")
        assert only == frozenset({"literature", "mature"}) and fmt == "txt"

    def test_the_literature_format_is_replaced_for_that_user(self, tmp_path):
        conf = load(tmp_path, {"artist": {"literature-format": "html"}})
        only, fmt = conf.for_user("artist", [WEB_WORK], frozenset({"images"}), "txt")
        # Untouched settings still come from the command line.
        assert only == frozenset({"images"}) and fmt == "html"

    def test_a_list_of_selectors_reads_like_the_repeated_flag(self, tmp_path):
        conf = load(tmp_path, {"artist": {"only": ["images", "no-ai"]}})
        only, _ = conf.for_user("artist", [WEB_WORK], None, "txt")
        assert only == frozenset({"images", "no-ai"})

    def test_an_empty_only_opts_out_of_a_run_wide_filter(self, tmp_path):
        conf = load(tmp_path, {"artist": {"only": ""}})
        only, _ = conf.for_user("artist", [WEB_WORK], frozenset({"mature"}), "txt")
        assert only == frozenset()

    def test_the_username_is_matched_whatever_its_case(self, tmp_path):
        conf = load(tmp_path, {"ArTist": {"literature-format": "html"}})
        assert conf.for_user("artist", [WEB_WORK], None, "txt")[1] == "html"

    def test_the_underscore_spelling_of_a_setting_is_read_too(self, tmp_path):
        conf = load(tmp_path, {"artist": {"literature_format": "html"}})
        assert conf.for_user("artist", [WEB_WORK], None, "txt")[1] == "html"

    def test_what_applied_is_reported(self, tmp_path, capsys):
        conf = load(tmp_path, {"artist": {"only": "images", "literature-format": "html"}})
        conf.for_user("artist", [WEB_WORK], None, "txt")
        out = capsys.readouterr().out
        assert "--only images" in out and "--literature-format html" in out


class TestLockedByTheCommandLine:
    """A flag given for the run outranks the file, which was written before it."""

    def test_a_locked_setting_is_left_to_the_command_line(self, tmp_path):
        path = write_file(tmp_path, {"artist": {"only": "literature"}})
        conf = ov.UserOverrides(path, frozenset({ov.ONLY}))
        only, _ = conf.for_user("artist", [WEB_WORK], frozenset({"images"}), "txt")
        assert only == frozenset({"images"})

    def test_what_the_command_line_left_out_still_comes_from_the_file(self, tmp_path):
        path = write_file(tmp_path, {"artist": {"only": "literature",
                                                "literature-format": "html"}})
        conf = ov.UserOverrides(path, frozenset({ov.ONLY}))
        only, fmt = conf.for_user("artist", [WEB_WORK], frozenset({"images"}), "txt")
        assert only == frozenset({"images"}) and fmt == "html"

    def test_a_locked_setting_is_not_reported_as_applied(self, tmp_path, capsys):
        path = write_file(tmp_path, {"artist": {"only": "literature"}})
        ov.UserOverrides(path, frozenset({ov.ONLY})).for_user(
            "artist", [WEB_WORK], None, "txt")
        assert "--only" not in capsys.readouterr().out

    def test_a_rename_is_still_followed_when_everything_is_locked(self, tmp_path):
        # Or the first run that leaves the flag off would find the entry stale.
        path = write_file(tmp_path, {"oldname": {"only": "literature",
                                                "ids": {"web": str(WEB_USER_ID)}}})
        conf = ov.UserOverrides(path, frozenset({ov.ONLY}))
        conf.for_user("newname", [WEB_WORK], None, "txt")
        assert list(read_back(tmp_path)) == ["newname"]

    def test_a_locked_setting_is_still_validated(self, tmp_path):
        # It is wrong today and would bite the first run without the flag.
        with pytest.raises(SystemExit, match="asks --only for sfw"):
            ov.UserOverrides(write_file(tmp_path, {"artist": {"only": "sfw"}}),
                             frozenset({ov.ONLY}))

    def test_shadowed_names_what_the_file_loses(self, tmp_path):
        path = write_file(tmp_path, {"artist": {"only": "literature"},
                                     "other": {"literature-format": "html"}})
        conf = ov.UserOverrides(path, frozenset({ov.ONLY, ov.FORMAT}))
        assert conf.shadowed() == frozenset({ov.ONLY, ov.FORMAT})

    def test_nothing_is_shadowed_when_the_file_is_silent_about_it(self, tmp_path):
        path = write_file(tmp_path, {"artist": {"literature-format": "html"}})
        conf = ov.UserOverrides(path, frozenset({ov.ONLY}))
        assert conf.shadowed() == frozenset()


class TestLearningIds:
    def test_the_id_of_a_named_user_is_recorded(self, tmp_path):
        conf = load(tmp_path, {"artist": {"only": "images"}})
        conf.for_user("artist", [WEB_WORK], None, "txt")
        assert read_back(tmp_path)["artist"]["ids"] == {"web": str(WEB_USER_ID)}

    def test_the_other_route_is_added_next_to_the_first(self, tmp_path):
        conf = load(tmp_path, {"artist": {"ids": {"web": str(WEB_USER_ID)}}})
        conf.for_user("artist", [make_dev()], None, "txt")
        assert read_back(tmp_path)["artist"]["ids"] == {
            "web": str(WEB_USER_ID), "api": API_USER_ID}

    def test_an_already_recorded_id_is_not_rewritten(self, tmp_path):
        path = write_file(tmp_path, {"artist": {"ids": {"web": str(WEB_USER_ID)}}})
        conf = ov.UserOverrides(path)
        before = path.stat().st_mtime_ns
        conf.for_user("artist", [WEB_WORK], None, "txt")
        assert path.stat().st_mtime_ns == before

    def test_a_file_that_cannot_be_written_only_warns(self, tmp_path, capsys,
                                                      monkeypatch):
        conf = load(tmp_path, {"artist": {"only": "images"}})
        monkeypatch.setattr(ov, "write_json",
                            lambda *a: (_ for _ in ()).throw(OSError("read-only")))
        only, _ = conf.for_user("artist", [WEB_WORK], None, "txt")
        # The settings still apply: what could not be written is the id.
        assert only == frozenset({"images"})
        assert "could not update" in capsys.readouterr().out


class TestRename:
    """A username can change; the id the routes report cannot."""

    def test_the_entry_moves_to_the_new_name(self, tmp_path, capsys):
        conf = load(tmp_path, {"oldname": {"only": "literature",
                                           "ids": {"web": str(WEB_USER_ID)}}})
        only, _ = conf.for_user("newname", [WEB_WORK], frozenset({"images"}), "txt")
        assert only == frozenset({"literature"})
        assert '"oldname" is now "newname"' in capsys.readouterr().out
        entries = read_back(tmp_path)
        assert "oldname" not in entries
        assert entries["newname"]["only"] == "literature"

    def test_the_recognised_id_survives_the_move(self, tmp_path):
        conf = load(tmp_path, {"oldname": {"only": "literature",
                                           "ids": {"web": str(WEB_USER_ID)}}})
        conf.for_user("newname", [WEB_WORK], None, "txt")
        assert read_back(tmp_path)["newname"]["ids"] == {"web": str(WEB_USER_ID)}

    def test_an_id_recorded_by_the_other_route_recognises_the_rename_too(self, tmp_path):
        conf = load(tmp_path, {"oldname": {"only": "literature",
                                           "ids": {"api": API_USER_ID}}})
        only, _ = conf.for_user("newname", [make_dev()], None, "txt")
        assert only == frozenset({"literature"})

    def test_the_same_number_under_another_route_is_not_the_same_user(self, tmp_path):
        # The two routes' ids are not comparable, so a value that matches under
        # the wrong route must not hand someone else's settings over.
        conf = load(tmp_path, {"oldname": {"only": "literature",
                                           "ids": {"api": str(WEB_USER_ID)}}})
        assert conf.for_user("newname", [WEB_WORK], None, "txt") == (None, "txt")

    def test_an_entry_no_run_has_touched_is_never_taken_for_a_rename(self, tmp_path):
        conf = load(tmp_path, {"oldname": {"only": "literature"}})
        assert conf.for_user("newname", [WEB_WORK], None, "txt") == (None, "txt")

    def test_a_name_that_matches_wins_over_an_id_that_matches(self, tmp_path):
        # Someone took the old name over: the entry keyed by the name asked for
        # is the answer, and the stale one is left where it is.
        conf = load(tmp_path, {"artist": {"only": "images"},
                               "other": {"only": "literature",
                                         "ids": {"web": str(WEB_USER_ID)}}})
        only, _ = conf.for_user("artist", [WEB_WORK], None, "txt")
        assert only == frozenset({"images"})
        assert "other" in read_back(tmp_path)


class TestBadFile:
    def test_damaged_json_stops_the_run(self, tmp_path):
        (tmp_path / ov.FILENAME).write_text("{not json", encoding="utf-8")
        with pytest.raises(SystemExit, match="Could not read the per-user settings"):
            ov.UserOverrides(tmp_path / ov.FILENAME)

    def test_a_list_instead_of_an_object_stops_the_run(self, tmp_path):
        with pytest.raises(SystemExit, match="one group of settings per username"):
            load(tmp_path, ["artist"])

    def test_a_username_naming_something_else_stops_the_run(self, tmp_path):
        with pytest.raises(SystemExit, match='"artist" must name a group'):
            load(tmp_path, {"artist": "images"})

    def test_an_unknown_setting_is_named(self, tmp_path):
        with pytest.raises(SystemExit, match='sets "unblur"'):
            load(tmp_path, {"artist": {"unblur": True}})

    def test_an_unknown_selector_is_named(self, tmp_path):
        with pytest.raises(SystemExit, match="asks --only for sfw"):
            load(tmp_path, {"artist": {"only": "images, sfw"}})

    def test_an_impossible_literature_format_is_named(self, tmp_path):
        with pytest.raises(SystemExit, match="asks --literature-format"):
            load(tmp_path, {"artist": {"literature-format": "pdf"}})

    def test_every_entry_is_checked_up_front(self, tmp_path):
        # Not just the user this run happens to reach: a typo left for weeks
        # would be found only once that user's turn came.
        with pytest.raises(SystemExit, match="asks --only for sfw"):
            load(tmp_path, {"artist": {"only": "images"},
                            "later": {"only": "sfw"}})


class TestLoadOverrides:
    def test_the_output_folder_is_where_it_looks_by_default(self, tmp_path):
        write_file(tmp_path, {"artist": {"only": "images"}})
        conf = ov.load_overrides(None, tmp_path)
        assert conf.path == tmp_path / ov.FILENAME
        assert conf.for_user("artist", [WEB_WORK], None, "txt")[0] == frozenset({"images"})

    def test_a_named_file_is_read_instead(self, tmp_path):
        elsewhere = tmp_path / "mine.json"
        elsewhere.write_text(json.dumps({"artist": {"only": "literature"}}),
                             encoding="utf-8")
        conf = ov.load_overrides(str(elsewhere), tmp_path)
        assert conf.for_user("artist", [WEB_WORK], None, "txt")[0] == frozenset(
            {"literature"})

    def test_a_named_file_that_is_not_there_stops_the_run(self, tmp_path):
        with pytest.raises(SystemExit, match="No per-user settings file"):
            ov.load_overrides(str(tmp_path / "nope.json"), tmp_path)

    def test_no_file_in_the_output_folder_is_not_an_error(self, tmp_path):
        assert ov.load_overrides(None, tmp_path)._entries == {}
