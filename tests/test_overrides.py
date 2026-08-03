"""The per-user settings file: what it sets, and surviving a rename."""

import json
import sys

import pytest

from deviantart_downloader import overrides as ov
from deviantart_downloader.constants import VERBOSE, WEB_SUBDIR

from .conftest import API_USER_ID, WEB_USER_ID, FakeStream, make_dev

WEB_WORK = {"_source": WEB_SUBDIR,
            "author": {"userid": WEB_USER_ID, "username": "artist"}}


def answering(monkeypatch, typed):
    """Somebody is at the terminal, and this is what they type at the question."""
    monkeypatch.setattr(ov, "_at_a_terminal", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": typed)


def write_file(tmp_path, entries):
    path = tmp_path / ov.FILENAME
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


def load(tmp_path, entries):
    return ov.UserOverrides(write_file(tmp_path, entries))


def read_back(tmp_path):
    return json.loads((tmp_path / ov.FILENAME).read_text(encoding="utf-8"))


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


class TestSkip:
    """Leaving a user out of a batch, answered before anything is fetched."""

    def test_a_user_marked_skip_is_skipped(self, tmp_path):
        conf = load(tmp_path, {"artist": {"skip": True}})
        assert conf.skips("artist") is True

    def test_the_name_is_matched_whatever_its_case(self, tmp_path):
        conf = load(tmp_path, {"ArTiSt": {"skip": True}})
        assert conf.skips("artist") is True

    @pytest.mark.parametrize("entries", [
        {},                                     # the file names nobody
        {"artist": {"skip": False}},
        {"artist": {"only": "images"}},         # named, but says nothing of skip
        {"someone-else": {"skip": True}},
    ])
    def test_everyone_else_is_left_alone(self, tmp_path, entries):
        assert load(tmp_path, entries).skips("artist") is False

    @pytest.mark.parametrize("written,expected", [
        (True, True), (False, False),
        # The words .env already accepts, so nobody has to learn a second set.
        ("true", True), ("yes", True), ("on", True), ("1", True),
        ("false", False), ("no", False), ("off", False), ("0", False),
    ])
    def test_it_reads_the_same_booleans_env_does(self, tmp_path, written, expected):
        conf = load(tmp_path, {"artist": {"skip": written}})
        assert conf.skips("artist") is expected

    def test_a_value_that_is_neither_ends_the_run(self, tmp_path):
        with pytest.raises(SystemExit, match="must be true or false"):
            load(tmp_path, {"artist": {"skip": "maybe"}})

    def test_skip_alone_does_not_announce_an_empty_line(self, tmp_path, capsys):
        """The line names the settings it applies, and skip is not one of them."""
        conf = load(tmp_path, {"artist": {"skip": False}})
        capsys.readouterr()          # the line loading printed, which is not this
        conf.for_user("artist", [WEB_WORK], None, "txt")
        assert ov.FILENAME not in capsys.readouterr().out

    def test_it_still_says_what_it_does_apply(self, tmp_path, capsys):
        conf = load(tmp_path, {"artist": {"skip": False, "only": "images"}})
        conf.for_user("artist", [WEB_WORK], None, "txt")
        assert "--only images" in capsys.readouterr().out

    def test_skip_leaves_the_other_settings_readable(self, tmp_path):
        conf = load(tmp_path, {"artist": {"skip": True, "only": "literature"}})
        assert conf.for_user("artist", [WEB_WORK], None, "txt") == (
            frozenset({"literature"}), "txt")


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


class TestReadingItIsAnnounced:
    def test_a_file_that_was_understood_says_so(self, tmp_path, capsys):
        load(tmp_path, {"artist": {"only": "images"}, "aWriter": {"skip": True}})
        assert (f"{ov.FILENAME}: read, settings for 2 user(s)."
                in capsys.readouterr().out)

    def test_a_file_with_nothing_in_it_yet_still_says_so(self, tmp_path, capsys):
        # Empty is not broken: somebody has made the file and not filled it in.
        load(tmp_path, {})
        assert "no settings in it yet" in capsys.readouterr().out

    def test_no_file_at_all_says_nothing(self, tmp_path, capsys):
        ov.UserOverrides(tmp_path / ov.FILENAME)
        assert capsys.readouterr().out == ""

    def test_the_line_is_the_green_one(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setattr(ov, "green", lambda text: f"<green>{text}")
        load(tmp_path, {"artist": {"only": "images"}})
        assert "<green>" in capsys.readouterr().out

    def test_a_quiet_run_drops_it(self, tmp_path, capsys):
        # Progress, not a result: -q wants what happened to the galleries, and
        # a settings file being read as intended is what was supposed to happen.
        VERBOSE.clear()
        load(tmp_path, {"artist": {"only": "images"}})
        assert capsys.readouterr().out == ""


class TestBadFile:
    def test_damaged_json_stops_the_run(self, tmp_path):
        (tmp_path / ov.FILENAME).write_text("{not json", encoding="utf-8")
        with pytest.raises(SystemExit, match="Could not read the per-user settings"):
            ov.UserOverrides(tmp_path / ov.FILENAME)

    def test_nothing_broken_is_reported_as_read(self, tmp_path, capsys):
        with pytest.raises(SystemExit):
            load(tmp_path, {"artist": {"only": "safe"}})
        assert "read" not in capsys.readouterr().out

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


class TestBadFileAtATerminal:
    """Somebody watching may decide a sync with the typed flags beats none."""

    def test_the_problem_is_put_in_front_of_them_first(self, tmp_path, monkeypatch,
                                                       capsys):
        answering(monkeypatch, "y")
        load(tmp_path, {"artist": {"only": "safe"}})
        out = capsys.readouterr().out
        assert "WARNING" in out and "asks --only for safe" in out
        assert "no user would get their own settings" in out

    def test_the_warning_is_the_orange_one(self, tmp_path, monkeypatch, capsys):
        answering(monkeypatch, "y")
        monkeypatch.setattr(ov, "orange", lambda text: f"<orange>{text}")
        load(tmp_path, {"artist": {"only": "safe"}})
        assert "<orange>" in capsys.readouterr().out

    @pytest.mark.parametrize("typed", ["y", "yes", "  YES  "])
    def test_a_yes_carries_on_with_nobody_configured(self, tmp_path, monkeypatch,
                                                     typed):
        answering(monkeypatch, typed)
        conf = load(tmp_path, {"artist": {"only": "safe"}})
        # Not the entry that could not be read, and not a half of it either:
        # the file is set aside whole, so every user gets the typed flags.
        assert conf.for_user("artist", [WEB_WORK], None, "txt") == (None, "txt")
        assert conf.skips("artist") is False
        assert conf.shadowed() == frozenset()

    @pytest.mark.parametrize("typed", ["n", "", "later", "Y E S"])
    def test_anything_short_of_a_yes_stops_the_run(self, tmp_path, monkeypatch, typed):
        answering(monkeypatch, typed)
        with pytest.raises(SystemExit, match="Stopped"):
            load(tmp_path, {"artist": {"only": "safe"}})

    def test_stdin_closing_mid_question_stops_the_run(self, tmp_path, monkeypatch):
        def closed(prompt=""):
            raise EOFError

        monkeypatch.setattr(ov, "_at_a_terminal", lambda: True)
        monkeypatch.setattr("builtins.input", closed)
        with pytest.raises(SystemExit, match="Stopped"):
            load(tmp_path, {"artist": {"only": "safe"}})

    def test_carrying_on_leaves_the_file_exactly_as_it_was(self, tmp_path, monkeypatch):
        # The one thing a rescued run must not do is write over the file its
        # owner still has to go and fix.
        path = tmp_path / ov.FILENAME
        path.write_text("{not json", encoding="utf-8")
        answering(monkeypatch, "y")
        conf = ov.UserOverrides(path)
        conf.for_user("artist", [WEB_WORK], None, "txt")
        assert path.read_text(encoding="utf-8") == "{not json"

    def test_with_nobody_there_it_stops_without_asking(self, tmp_path, monkeypatch):
        # A pipe, a cron job, CI: assuming yes would hand the decision to
        # whoever reads the log afterwards, which is too late for a decision.
        asked = []
        monkeypatch.setattr("builtins.input", lambda prompt="": asked.append(prompt))
        monkeypatch.setattr(ov, "_at_a_terminal", lambda: False)
        with pytest.raises(SystemExit, match="asks --only for safe"):
            load(tmp_path, {"artist": {"only": "safe"}})
        assert asked == []


class TestAtATerminal:
    def test_a_pipe_is_nobody(self, monkeypatch):
        monkeypatch.setattr(sys, "stdin", FakeStream(False))
        assert ov._at_a_terminal() is False

    def test_a_terminal_is_somebody(self, monkeypatch):
        monkeypatch.setattr(sys, "stdin", FakeStream(True))
        assert ov._at_a_terminal() is True

    @pytest.mark.parametrize("gone", [ValueError, OSError])
    def test_a_stdin_already_closed_is_nobody(self, monkeypatch, gone):
        monkeypatch.setattr(sys, "stdin", FakeStream(True, gone))
        assert ov._at_a_terminal() is False


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
