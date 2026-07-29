"""Live keyboard controls: the key handler, the pause gate and the listener."""

import io
import os
import threading
import time

import pytest

from deviantart_downloader import constants, controls
from deviantart_downloader.constants import CANCEL, RESUME


def _wait_until(pred, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


class TestApplyKey:
    def test_p_pauses_and_r_resumes(self):
        assert RESUME.is_set()
        assert controls.apply_key("p") is True and not RESUME.is_set()
        assert controls.apply_key("r") is True and RESUME.is_set()

    def test_keys_are_case_insensitive(self):
        assert controls.apply_key("P") is True and not RESUME.is_set()
        assert controls.apply_key("R") is True and RESUME.is_set()

    def test_pause_when_already_paused_is_a_no_op(self):
        controls.apply_key("p")
        assert controls.apply_key("p") is False

    def test_resume_when_running_is_a_no_op(self):
        assert controls.apply_key("r") is False

    def test_q_cancels_and_wakes_paused_workers(self):
        controls.apply_key("p")
        assert controls.apply_key("q") is True
        assert CANCEL.is_set()
        assert RESUME.is_set()            # paused workers are released to abort

    def test_unknown_key_is_ignored(self):
        assert controls.apply_key("x") is False


class TestFooterText:
    def test_reflects_the_run_state(self):
        assert "running" in controls.footer_text()
        assert "pause" in controls.footer_text()
        controls.apply_key("p")
        assert "PAUSED" in controls.footer_text()
        controls.apply_key("q")
        assert "quitting" in controls.footer_text()


class TestWaitIfPaused:
    def test_returns_immediately_when_running(self):
        start = time.monotonic()
        constants.wait_if_paused()
        assert time.monotonic() - start < 0.1

    def test_blocks_until_resumed(self):
        RESUME.clear()
        released = threading.Event()

        def worker():
            constants.wait_if_paused()
            released.set()

        t = threading.Thread(target=worker)
        t.start()
        assert not released.wait(0.2)     # still blocked while paused
        RESUME.set()
        assert released.wait(1)           # released once resumed
        t.join()

    def test_cancel_wakes_a_paused_worker(self):
        RESUME.clear()
        released = threading.Event()
        t = threading.Thread(target=lambda: (constants.wait_if_paused(),
                                             released.set()))
        t.start()
        assert not released.wait(0.1)
        CANCEL.set()                      # cancel must wake it even while paused
        assert released.wait(1)
        t.join()


class TestFooterWriter:
    def test_pins_footer_below_each_line(self):
        buf = io.StringIO()
        w = controls._FooterWriter(buf)
        w.set_footer(["FOOT"])
        w.write("hello\n")
        w.write("world\n")
        out = buf.getvalue()
        assert "hello\n" in out and "world\n" in out
        assert "\x1b[2K" in out           # the footer line is cleared each time
        assert out.endswith("FOOT")       # footer ends up pinned at the bottom

    def test_partial_writes_buffer_until_newline(self):
        buf = io.StringIO()
        w = controls._FooterWriter(buf)
        w.write("ab")                     # no newline yet: nothing emitted
        assert "ab" not in buf.getvalue()
        w.write("c\n")
        assert "abc\n" in buf.getvalue()

    def test_clear_footer_erases_the_line(self):
        buf = io.StringIO()
        w = controls._FooterWriter(buf)
        w.set_footer("F")
        w.clear_footer()
        assert buf.getvalue().endswith("\r\x1b[2K")

    def test_delegates_unknown_attributes_to_the_stream(self):
        buf = io.StringIO()
        w = controls._FooterWriter(buf)
        assert w.getvalue() == ""         # delegated to StringIO


class FakeTTY:
    """Minimal readable, always-ready stream for driving the listener loop."""

    def __init__(self, keys):
        self._keys = list(keys)

    def read(self, n):
        return self._keys.pop(0) if self._keys else ""

    def isatty(self):
        return True

    def fileno(self):
        return -1


class TestRateLimitHold:
    """A 429 used to print a line per worker per attempt; now it is one place."""

    def test_no_hold_leaves_the_footer_at_one_line(self):
        assert len(controls.footer_lines(width=96)) == 1

    def test_a_hold_adds_a_line_above_the_status(self):
        controls.set_progress("12/900  api  X")
        controls.set_hold(28)
        lines = controls.footer_lines(width=96)
        assert len(lines) == 2
        assert lines[0].startswith("[rate limit]")
        # The keys stay on the last line, where the eye already looks for them.
        assert lines[1].endswith("[q] quit")

    def test_it_counts_down(self, monkeypatch):
        clock = [1000.0]
        monkeypatch.setattr(controls.time, "monotonic", lambda: clock[0])
        controls.set_hold(30)
        assert "resuming in 30s" in controls.hold_line(width=96)
        clock[0] += 22
        assert "resuming in 8s" in controls.hold_line(width=96)
        clock[0] += 10
        assert controls.footer_lines(width=96) == [controls.footer_text(96)]

    def test_the_429s_of_one_stall_are_counted_together(self):
        controls.set_hold(30)                    # first worker to notice
        for _ in range(4):
            controls.set_hold(28)                # the rest pile onto the same wait
        assert "429s so far: 5" in controls.hold_line(width=96)

    def test_a_request_getting_through_ends_it(self):
        controls.set_hold(30)
        controls.clear_hold()
        assert controls.hold_seconds() == 0
        assert len(controls.footer_lines(width=96)) == 1

    def test_off_a_terminal_a_stall_announces_itself_once(self, capsys):
        for seconds in (4, 8, 16, 32):           # the ladder, as one stall
            controls.set_hold(seconds)
        out = capsys.readouterr().out
        assert out.count("Rate limit reached") == 1
        assert "Waiting 4s" in out


class TestRedrawSeam:
    """One place decides whether there is a footer and what goes in it."""

    def test_it_reports_when_there_is_nothing_to_paint(self):
        assert controls._redraw() is False        # stdout is not the wrapper here

    def test_the_first_paint_is_one_line_not_one_per_character(self, monkeypatch):
        """A str is iterable, so handing the writer raw text painted it vertically."""
        buf = io.StringIO()
        writer = controls._FooterWriter(buf)
        monkeypatch.setattr(controls.sys, "stdout", writer)
        assert controls._redraw() is True
        assert len(writer._footer) == 1
        assert "\x1b[1A" not in buf.getvalue()


class TestFooterBlockRedraw:
    """The block is one line or two, so the writer must erase what it drew."""

    def lines_of(self, out):
        return out.count("\x1b[2K")

    def test_growing_to_two_lines_erases_the_one_that_was_there(self):
        buf = io.StringIO()
        w = controls._FooterWriter(buf)
        w.set_footer(["ONE"])
        buf.seek(0); buf.truncate()
        w.set_footer(["HOLD", "ONE"])
        out = buf.getvalue()
        assert out.startswith("\r\x1b[2K")          # one line erased, not two
        assert "\x1b[1A" not in out
        assert out.endswith("HOLD\nONE")

    def test_shrinking_back_erases_both(self):
        buf = io.StringIO()
        w = controls._FooterWriter(buf)
        w.set_footer(["HOLD", "ONE"])
        buf.seek(0); buf.truncate()
        w.set_footer(["ONE"])
        out = buf.getvalue()
        assert out.count("\x1b[1A") == 1           # walked up over the extra line
        assert out.endswith("ONE")

    def test_a_printed_line_scrolls_above_a_two_line_block(self):
        buf = io.StringIO()
        w = controls._FooterWriter(buf)
        w.set_footer(["HOLD", "ONE"])
        buf.seek(0); buf.truncate()
        w.write("done\n")
        out = buf.getvalue()
        assert out.count("\x1b[1A") == 1           # both footer lines cleared
        assert "done\n" in out and out.endswith("HOLD\nONE")

    def test_clearing_walks_up_the_whole_block(self):
        buf = io.StringIO()
        w = controls._FooterWriter(buf)
        w.set_footer(["HOLD", "ONE"])
        buf.seek(0); buf.truncate()
        w.clear_footer()
        assert buf.getvalue().count("\x1b[1A") == 1


class TestKeyboardControls:
    def test_inactive_without_a_tty(self):
        # A plain StringIO is not a TTY, so the controls stay inactive.
        with controls.KeyboardControls(stream=io.StringIO("pq")) as kc:
            assert kc.active is False

    def test_listen_processes_keys_until_quit(self, monkeypatch):
        monkeypatch.setattr(controls.select, "select",
                            lambda r, w, x, t: (r, [], []))   # always ready
        kc = controls.KeyboardControls(stream=FakeTTY(["p", "q"]))
        kc._listen()                        # returns when 'q' is read
        assert CANCEL.is_set()              # 'p' then 'q' were both applied

    @pytest.mark.skipif(not controls._HAS_TERMIOS, reason="POSIX terminal only")
    def test_real_pty_pause_resume_quit(self):
        """Drive the full listener over a real pseudo-terminal."""
        import pty

        master, slave = pty.openpty()
        stream = os.fdopen(slave, "r", buffering=1)
        with controls.KeyboardControls(stream=stream) as kc:
            assert kc.active is True
            os.write(master, b"p")
            assert _wait_until(lambda: not RESUME.is_set())
            os.write(master, b"r")
            assert _wait_until(RESUME.is_set)
            os.write(master, b"q")
            assert _wait_until(CANCEL.is_set)
        assert kc.active is False           # __exit__ restored the terminal
        os.close(master)


class TestFooterProgress:
    """Under -q the footer is the only progress a run shows, so it carries it."""

    KEYS = "keys: [p] pause  [r] resume  [q] quit"

    def test_progress_sits_between_the_state_and_the_keys(self):
        controls.set_progress("42/900  Crystal ID")
        text = controls.footer_text(width=200)
        assert text.index("[running]") < text.index("42/900") < text.index("keys:")

    def test_the_keys_end_at_the_same_column_whatever_the_progress_says(self):
        """The complaint this exists for: work names vary in length, and a hint
        that slides about with them cannot be read while it moves."""
        ends = set()
        for progress in ("", "1/900  api  X", "874/900  web  A very long title here"):
            controls.set_progress(progress)
            text = controls.footer_text(width=100)
            assert text.endswith(self.KEYS)
            ends.add(len(text))
        assert ends == {99}          # one column short of the edge, always

    def test_an_overlong_progress_is_cut_and_the_keys_stay_put(self):
        controls.set_progress("9/900  api  " + "a" * 300)
        text = controls.footer_text(width=100)
        assert text.endswith(self.KEYS) and len(text) == 99
        assert "…" in text           # the progress lost its tail, not the keys

    def test_no_progress_still_reaches_the_right_edge(self):
        text = controls.footer_text(width=200)
        assert text.startswith("[running]") and text.endswith(self.KEYS)
        assert len(text) == 199

    def test_a_terminal_too_narrow_to_split_falls_back_to_one_run(self):
        controls.set_progress("42/900  Crystal ID")
        text = controls.footer_text(width=30)
        assert len(text) == 29 and "42/900" in text and "resume" not in text

    def test_progress_survives_a_pause_and_a_quit(self):
        # TestFooterText already pins the state strings; this pins that the
        # progress rides through a state change rather than being reset by it.
        controls.set_progress("42/900")
        for key in "pq":
            controls.apply_key(key)
            assert "42/900" in controls.footer_text(width=200)

    def test_off_a_terminal_it_records_without_drawing(self):
        # stdout is not the footer writer here, so there is nothing to draw on.
        controls.set_progress("42/900")      # must not raise
        assert controls._PROGRESS == "42/900"

    def test_leaving_the_controls_clears_the_progress(self):
        controls.set_progress("42/900")
        with controls.KeyboardControls(stream=io.StringIO()):
            pass
        assert controls._PROGRESS == ""      # the next user starts clean
        assert controls.footer_text(width=200).split()[0] == "[running]"
