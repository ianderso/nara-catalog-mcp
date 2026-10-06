"""Configuration: the key, the optional settings, and where .env is read from."""

from __future__ import annotations

from pathlib import Path

import pytest

from nara_catalog_mcp.config import DEFAULT_CALL_BUDGET, ConfigError, load_config

SETTINGS = ("NARA_API_KEY", "NARA_CACHE_DIR", "NARA_TIMEOUT", "NARA_MONTHLY_CALL_BUDGET")


@pytest.fixture
def isolated(tmp_path, monkeypatch) -> Path:
    """Start each test in an empty directory with no NARA settings.

    ``load_dotenv`` writes into ``os.environ``. Setting each variable before
    deleting it makes monkeypatch remove it again on teardown, whatever a
    ``.env`` put there in between -- so nothing leaks into other tests.
    """
    for name in SETTINGS:
        monkeypatch.setenv(name, "set-by-fixture")
        monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_a_missing_key_is_a_config_error_naming_it(isolated):
    """The first thing a new user hits; it has to say what to do."""
    with pytest.raises(ConfigError, match="NARA_API_KEY is not set"):
        load_config()


def test_the_example_placeholder_is_not_accepted_as_a_key(isolated, monkeypatch):
    """Copying .env.example without editing it would otherwise just 401."""
    monkeypatch.setenv("NARA_API_KEY", "your-key-here")
    with pytest.raises(ConfigError, match="placeholder"):
        load_config()


def test_surrounding_whitespace_is_not_part_of_the_key(isolated, monkeypatch):
    """A key pasted with a trailing space or newline would be rejected."""
    monkeypatch.setenv("NARA_API_KEY", "  abc123\n")
    assert load_config().api_key == "abc123"


def test_defaults_apply_when_only_the_key_is_given(isolated, monkeypatch):
    monkeypatch.setenv("NARA_API_KEY", "abc123")
    cfg = load_config()
    assert cfg.timeout == 60.0
    assert cfg.monthly_call_budget == DEFAULT_CALL_BUDGET
    assert cfg.cache_dir == Path.home() / ".cache" / "nara-catalog-mcp"


def test_optional_settings_are_parsed(isolated, monkeypatch):
    monkeypatch.setenv("NARA_API_KEY", "abc123")
    monkeypatch.setenv("NARA_CACHE_DIR", "~/nara-cache")
    monkeypatch.setenv("NARA_TIMEOUT", "12.5")
    monkeypatch.setenv("NARA_MONTHLY_CALL_BUDGET", "2500")
    cfg = load_config()
    assert cfg.cache_dir == Path.home() / "nara-cache"
    assert cfg.timeout == 12.5
    assert cfg.monthly_call_budget == 2500


@pytest.mark.parametrize(
    ("name", "raw"),
    [
        ("NARA_TIMEOUT", "sixty"),
        ("NARA_TIMEOUT", "0"),
        ("NARA_TIMEOUT", "-5"),
        ("NARA_TIMEOUT", "inf"),
        ("NARA_TIMEOUT", "nan"),
        ("NARA_MONTHLY_CALL_BUDGET", "10k"),
        ("NARA_MONTHLY_CALL_BUDGET", "2.5"),
        ("NARA_MONTHLY_CALL_BUDGET", "0"),
    ],
)
def test_an_unusable_number_names_its_variable(isolated, monkeypatch, name, raw):
    """'could not convert string to float' does not say which setting is wrong."""
    monkeypatch.setenv("NARA_API_KEY", "abc123")
    monkeypatch.setenv(name, raw)
    with pytest.raises(ConfigError, match=name):
        load_config()


def test_a_dotenv_in_the_working_directory_is_read(isolated):
    """The documented setup: a .env beside where the server is started."""
    (isolated / ".env").write_text("NARA_API_KEY=from-dotenv\nNARA_TIMEOUT=7\n")
    cfg = load_config()
    assert cfg.api_key == "from-dotenv"
    assert cfg.timeout == 7.0


def test_the_real_environment_beats_the_dotenv(isolated, monkeypatch):
    """A client config's env block must not be overridden by a stray file."""
    (isolated / ".env").write_text("NARA_API_KEY=from-dotenv\n")
    monkeypatch.setenv("NARA_API_KEY", "from-environment")
    assert load_config().api_key == "from-environment"


def test_a_dotenv_in_a_parent_directory_is_not_read(isolated, monkeypatch):
    """Only the working directory counts.

    An upward search would read whatever .env sits in a parent -- the home
    directory, say -- which is not a file anyone meant for this server.
    """
    (isolated / ".env").write_text("NARA_API_KEY=from-parent\n")
    child = isolated / "child"
    child.mkdir()
    monkeypatch.chdir(child)
    with pytest.raises(ConfigError, match="NARA_API_KEY is not set"):
        load_config()


def test_the_aad_tools_can_load_settings_without_a_key(isolated, monkeypatch):
    """AAD takes no key, so its tools must not be refused for want of one."""
    monkeypatch.setenv("NARA_CACHE_DIR", str(isolated / "cache"))
    cfg = load_config(require_key=False)
    assert cfg.api_key == ""
    assert cfg.cache_dir == isolated / "cache"
    monkeypatch.setenv("NARA_API_KEY", "your-key-here")
    assert load_config(require_key=False).api_key == ""
    with pytest.raises(ConfigError):
        load_config()


def test_settings_are_still_checked_without_a_key(isolated, monkeypatch):
    """Not needing a key does not make a bad timeout acceptable."""
    monkeypatch.setenv("NARA_TIMEOUT", "sixty")
    with pytest.raises(ConfigError, match="NARA_TIMEOUT"):
        load_config(require_key=False)
