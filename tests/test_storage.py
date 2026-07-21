"""The JSON files a gallery folder keeps, and how they survive a bad run."""

import json

import pytest

from deviantart_downloader import storage


class TestReadJson:
    def test_reads_a_file(self, tmp_path):
        path = tmp_path / "data.json"
        path.write_text('{"a": 1}', encoding="utf-8")
        assert storage.read_json(path, {}) == {"a": 1}

    def test_missing_file_returns_the_default_quietly(self, tmp_path, capsys):
        assert storage.read_json(tmp_path / "nope.json", {"d": 1}) == {"d": 1}
        assert capsys.readouterr().out == ""

    def test_damaged_file_warns_and_falls_back(self, tmp_path, capsys):
        path = tmp_path / "data.json"
        path.write_text("{not json", encoding="utf-8")
        assert storage.read_json(path, {}) == {}
        assert "WARNING: could not read data.json" in capsys.readouterr().out

    @pytest.mark.parametrize("content,default", [
        ("[1, 2]", {}),      # a list where a mapping was expected
        ('{"a": 1}', []),    # a mapping where a list was expected
        ("null", {}),
    ])
    def test_unexpected_shape_warns_and_falls_back(self, tmp_path, capsys,
                                                   content, default):
        path = tmp_path / "data.json"
        path.write_text(content, encoding="utf-8")
        assert storage.read_json(path, default) == default
        assert "holds unexpected data" in capsys.readouterr().out


class TestWriteJson:
    def test_round_trips(self, tmp_path):
        path = tmp_path / "data.json"
        storage.write_json(path, {"a": [1, "ñ"]})
        assert json.loads(path.read_text(encoding="utf-8")) == {"a": [1, "ñ"]}

    def test_leaves_no_temporary_behind(self, tmp_path):
        storage.write_json(tmp_path / "data.json", {"a": 1})
        assert [p.name for p in tmp_path.iterdir()] == ["data.json"]

    def test_a_failed_write_leaves_the_previous_version_intact(self, tmp_path,
                                                               monkeypatch):
        """The point of the temporary file: no half-written JSON on disk."""
        path = tmp_path / "data.json"
        storage.write_json(path, {"good": True})

        def die(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(json, "dumps", die)
        with pytest.raises(OSError):
            storage.write_json(path, {"replacement": True})
        assert json.loads(path.read_text(encoding="utf-8")) == {"good": True}
