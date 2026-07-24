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
        msg = controls.apply_key("p")
        assert "Paused" in msg and not RESUME.is_set()
        msg = controls.apply_key("r")
        assert "Resumed" in msg and RESUME.is_set()

    def test_keys_are_case_insensitive(self):
        controls.apply_key("P")
        assert not RESUME.is_set()
        controls.apply_key("R")
        assert RESUME.is_set()

    def test_pause_when_already_paused_is_a_no_op(self):
        controls.apply_key("p")
        assert controls.apply_key("p") is None

    def test_resume_when_running_is_a_no_op(self):
        assert controls.apply_key("r") is None

    def test_q_cancels_and_wakes_paused_workers(self):
        controls.apply_key("p")
        msg = controls.apply_key("q")
        assert "Quitting" in msg
        assert CANCEL.is_set()
        assert RESUME.is_set()            # paused workers are released to abort

    def test_unknown_key_is_ignored(self):
        assert controls.apply_key("x") is None


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


class TestKeyboardControls:
    def test_inactive_without_a_tty(self):
        # A plain StringIO is not a TTY, so the controls stay inactive.
        with controls.KeyboardControls(stream=io.StringIO("pq")) as kc:
            assert kc.active is False

    def test_listen_processes_keys_until_quit(self, monkeypatch, capsys):
        monkeypatch.setattr(controls.select, "select",
                            lambda r, w, x, t: (r, [], []))   # always ready
        kc = controls.KeyboardControls(stream=FakeTTY(["p", "q"]))
        kc._listen()                        # returns when 'q' is read

        assert CANCEL.is_set()
        out = capsys.readouterr().out
        assert "Paused" in out and "Quitting" in out

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
