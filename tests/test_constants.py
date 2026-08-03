"""The shared flags and helpers, and when a line comes out coloured."""

import sys

import pytest

from deviantart_downloader import constants as c

from .conftest import FakeStream


@pytest.fixture(autouse=True)
def no_color_unset(monkeypatch):
    """Whatever the machine running the tests prefers, these decide for themselves."""
    monkeypatch.delenv("NO_COLOR", raising=False)


class TestColour:
    @pytest.mark.parametrize("paint,code", [(c.green, c.GREEN), (c.orange, c.ORANGE)])
    def test_a_terminal_gets_the_escapes(self, paint, code):
        assert paint("read", FakeStream(True)) == f"{code}read{c.RESET}"

    @pytest.mark.parametrize("paint", [c.green, c.orange])
    def test_anything_else_gets_the_text_as_it_was(self, paint):
        # Redirected into a file or piped into another program, the escapes
        # would be noise sitting in the middle of the text.
        assert paint("read", FakeStream(False)) == "read"

    @pytest.mark.parametrize("paint", [c.green, c.orange])
    def test_no_color_is_obeyed_even_at_a_terminal(self, paint, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        assert paint("read", FakeStream(True)) == "read"

    @pytest.mark.parametrize("gone", [ValueError, OSError])
    def test_a_stream_already_closed_is_not_an_error(self, gone):
        assert c.green("read", FakeStream(True, gone)) == "read"

    def test_stdout_is_the_default_and_is_read_when_asked(self, monkeypatch):
        # Not once at import: stdout is swapped for the footer writer while a
        # run is going, so the answer changes underneath this.
        monkeypatch.setattr(sys, "stdout", FakeStream(True))
        assert c.green("read") == f"{c.GREEN}read{c.RESET}"
        monkeypatch.setattr(sys, "stdout", FakeStream(False))
        assert c.green("read") == "read"
