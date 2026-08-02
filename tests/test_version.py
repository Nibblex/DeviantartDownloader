"""The version, and the single place it is allowed to be written.

The point of these is not that the number is right -- nothing here can know
that -- but that there is still only one of it. A hardcoded `version` put back
into pyproject would work perfectly until the day the two copies disagreed, and
the release that shipped the wrong one would be permanent on PyPI.
"""

import importlib.util
import re
from pathlib import Path

import pytest

import deviantart_downloader
from deviantart_downloader.__version__ import __version__

REPO_ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = REPO_ROOT / "deviantart_downloader" / "__version__.py"
ATTR = "deviantart_downloader.__version__.__version__"


class TestVersion:
    def test_it_looks_like_a_version(self):
        assert re.fullmatch(r"\d+\.\d+\.\d+[0-9a-z.]*", __version__), __version__

    def test_the_package_reports_the_same_one(self):
        assert deviantart_downloader.__version__ == __version__

    def test_it_is_part_of_what_the_package_exports(self):
        assert "__version__" in deviantart_downloader.__all__


class TestOnlyOnePlaceWritesIt:
    def config(self):
        # tomllib arrived in 3.11 and this project still supports 3.10, where
        # the check simply does not run; CI covers it on the other four.
        tomllib = pytest.importorskip("tomllib")
        return tomllib.loads(REPO_ROOT.joinpath("pyproject.toml").read_text())

    def test_pyproject_does_not_spell_the_version_out_as_well(self):
        assert "version" not in self.config()["project"], (
            "pyproject carries its own copy of the version again; it should "
            f"stay dynamic and read {VERSION_FILE.name}")

    def test_pyproject_declares_it_dynamic_and_points_here(self):
        config = self.config()
        assert "version" in config["project"]["dynamic"]
        assert config["tool"]["setuptools"]["dynamic"]["version"]["attr"] == ATTR

    def test_the_file_can_be_read_without_importing_the_package(self):
        """What setuptools and the release workflow both do.

        Importing deviantart_downloader needs requests installed, and neither a
        build machine nor the tag check has a reason to have it, so the file has
        to stand on its own.
        """
        spec = importlib.util.spec_from_file_location("standalone", VERSION_FILE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.__version__ == __version__

    def test_nothing_else_in_the_package_writes_a_version(self):
        """A second assignment anywhere would be the copy that drifts."""
        others = [
            path.name
            for path in REPO_ROOT.joinpath("deviantart_downloader").glob("*.py")
            if path != VERSION_FILE
            and re.search(r"^__version__\s*=", path.read_text(), re.M)
        ]
        assert others == []
