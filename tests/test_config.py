"""Environment and .env handling."""

import os

import pytest

from deviantart_downloader import config


class TestLoadDotenv:
    def test_parses_values_and_ignores_noise(self, tmp_path, monkeypatch):
        env = tmp_path / ".env"
        env.write_text(
            "# a comment\n"
            "\n"
            "TESTDD_PLAIN=hello\n"
            "TESTDD_QUOTED='quoted value'\n"
            "TESTDD_SPACED =  padded  \n"
            "not-a-valid-line\n",
            encoding="utf-8",
        )
        monkeypatch.delenv("TESTDD_PLAIN", raising=False)
        config.load_dotenv(env)
        try:
            assert os.environ["TESTDD_PLAIN"] == "hello"
            assert os.environ["TESTDD_QUOTED"] == "quoted value"
            assert os.environ["TESTDD_SPACED"] == "padded"
        finally:
            for key in ("TESTDD_PLAIN", "TESTDD_QUOTED", "TESTDD_SPACED"):
                os.environ.pop(key, None)

    def test_does_not_overwrite_existing_variables(self, tmp_path, monkeypatch):
        env = tmp_path / ".env"
        env.write_text("TESTDD_KEEP=from_file\n", encoding="utf-8")
        monkeypatch.setenv("TESTDD_KEEP", "original")
        config.load_dotenv(env)
        assert os.environ["TESTDD_KEEP"] == "original"

    def test_missing_file_is_a_no_op(self, tmp_path):
        config.load_dotenv(tmp_path / "does-not-exist")

    def test_discovers_env_in_cwd(self, tmp_path, monkeypatch):
        (tmp_path / ".env").write_text("TESTDD_CWD=yes\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("TESTDD_CWD", raising=False)
        config.load_dotenv()
        try:
            assert os.environ["TESTDD_CWD"] == "yes"
        finally:
            os.environ.pop("TESTDD_CWD", None)


class TestEnvInt:
    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("TESTDD_INT", raising=False)
        assert config.env_int("TESTDD_INT", 7) == 7

    def test_reads_integer(self, monkeypatch):
        monkeypatch.setenv("TESTDD_INT", " 12 ")
        assert config.env_int("TESTDD_INT", 7) == 12

    def test_invalid_value_exits(self, monkeypatch):
        monkeypatch.setenv("TESTDD_INT", "banana")
        with pytest.raises(SystemExit):
            config.env_int("TESTDD_INT", 7)


class TestEnvFloat:
    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("TESTDD_FLOAT", raising=False)
        assert config.env_float("TESTDD_FLOAT", 0.5) == 0.5

    def test_reads_float(self, monkeypatch):
        monkeypatch.setenv("TESTDD_FLOAT", " 1.5 ")
        assert config.env_float("TESTDD_FLOAT", 0.5) == 1.5

    def test_invalid_value_exits(self, monkeypatch):
        monkeypatch.setenv("TESTDD_FLOAT", "banana")
        with pytest.raises(SystemExit):
            config.env_float("TESTDD_FLOAT", 0.5)


class TestEnvBool:
    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("TESTDD_BOOL", raising=False)
        assert config.env_bool("TESTDD_BOOL", True) is True
        assert config.env_bool("TESTDD_BOOL", False) is False

    @pytest.mark.parametrize("value", ["1", "true", "YES", "On"])
    def test_truthy_values(self, monkeypatch, value):
        monkeypatch.setenv("TESTDD_BOOL", value)
        assert config.env_bool("TESTDD_BOOL", False) is True

    @pytest.mark.parametrize("value", ["0", "false", "NO", "Off"])
    def test_falsy_values(self, monkeypatch, value):
        monkeypatch.setenv("TESTDD_BOOL", value)
        assert config.env_bool("TESTDD_BOOL", True) is False

    def test_invalid_value_exits(self, monkeypatch):
        monkeypatch.setenv("TESTDD_BOOL", "maybe")
        with pytest.raises(SystemExit):
            config.env_bool("TESTDD_BOOL", False)
