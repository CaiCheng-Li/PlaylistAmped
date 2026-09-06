"""Config storage, the rename migration, and sign-out."""

import pytest

from playlistamped import config as config_mod
from playlistamped.app import build_parser, free_port
from playlistamped.config import Config


@pytest.fixture
def dirs(monkeypatch, tmp_path):
    """Redirect every config/cache path into a temp tree."""
    paths = {
        "config": tmp_path / "config",
        "cache": tmp_path / "cache",
        "legacy_config": tmp_path / "legacy_config",
        "legacy_cache": tmp_path / "legacy_cache",
    }
    monkeypatch.setattr(config_mod, "config_dir", lambda: paths["config"])
    monkeypatch.setattr(config_mod, "cache_dir", lambda: paths["cache"])
    monkeypatch.setattr(
        config_mod,
        "user_config_dir",
        lambda app, appauthor=None: str(
            paths["legacy_config"] if app == config_mod.LEGACY_APP else paths["config"]
        ),
    )
    monkeypatch.setattr(
        config_mod,
        "user_cache_dir",
        lambda app, appauthor=None: str(
            paths["legacy_cache"] if app == config_mod.LEGACY_APP else paths["cache"]
        ),
    )
    return paths


def test_round_trip(dirs):
    config_mod.save(Config(baseurl="http://x:32400", token="t", section="Tunes"))
    loaded = config_mod.load()
    assert loaded.baseurl == "http://x:32400"
    assert loaded.section == "Tunes"


def test_thresholds_survive_a_round_trip(dirs):
    config_mod.save(Config(baseurl="u", token="t", auto_accept=91.0, review_floor=70.0))
    loaded = config_mod.load()
    assert (loaded.auto_accept, loaded.review_floor) == (91.0, 70.0)


def test_values_with_quotes_and_backslashes_survive(dirs):
    config_mod.save(Config(baseurl='http://a\\b"c', token="t"))
    assert config_mod.load().baseurl == 'http://a\\b"c'


def test_env_overrides_the_file(dirs, monkeypatch):
    config_mod.save(Config(baseurl="http://file:32400", token="filetoken"))
    monkeypatch.setenv("PLEX_URL", "http://env:32400")
    assert config_mod.load().baseurl == "http://env:32400"


def test_legacy_settings_are_adopted(dirs):
    """Renaming the project must not force a re-login and a full re-index."""
    dirs["legacy_config"].mkdir(parents=True)
    (dirs["legacy_config"] / "config.toml").write_text(
        '[plex]\nbaseurl = "http://old:32400"\ntoken = "kept"\nsection = "Music"\n',
        encoding="utf-8",
    )
    dirs["legacy_cache"].mkdir(parents=True)
    (dirs["legacy_cache"] / "decisions.json").write_text("{}", encoding="utf-8")

    loaded = config_mod.load()
    assert loaded.baseurl == "http://old:32400"
    assert loaded.token == "kept"
    assert (dirs["cache"] / "decisions.json").exists()


def test_migration_never_overwrites_current_settings(dirs):
    config_mod.save(Config(baseurl="http://current:32400", token="current"))
    dirs["legacy_config"].mkdir(parents=True)
    (dirs["legacy_config"] / "config.toml").write_text(
        '[plex]\nbaseurl = "http://old:32400"\n', encoding="utf-8"
    )
    assert config_mod.load().baseurl == "http://current:32400"


def test_sign_out_clears_credentials_and_library(dirs):
    config_mod.save(Config(baseurl="u", token="secret", account_token="also-secret"))
    index = dirs["cache"] / "index"
    index.mkdir(parents=True)
    (index / "server-1.json").write_text("{}", encoding="utf-8")
    (dirs["cache"] / "decisions.json").write_text('{"a": {}}', encoding="utf-8")

    config_mod.sign_out()

    assert not config_mod.config_path().exists()
    assert not index.exists()
    assert config_mod.load().has_connection() is False
    # Decisions are keyed by source track id, not by server, so they survive.
    assert (dirs["cache"] / "decisions.json").exists()


def test_sign_out_can_also_forget_decisions(dirs):
    config_mod.save(Config(baseurl="u", token="t"))
    (dirs["cache"]).mkdir(parents=True, exist_ok=True)
    (dirs["cache"] / "decisions.json").write_text('{"a": {}}', encoding="utf-8")
    config_mod.sign_out(forget_decisions=True)
    assert not (dirs["cache"] / "decisions.json").exists()


def test_sign_out_on_a_fresh_install_is_harmless(dirs):
    config_mod.sign_out()  # must not raise when nothing exists


@pytest.mark.parametrize(
    "cfg,expected",
    [
        (Config(), False),
        (Config(account_token="a", server_name="s"), True),
        (Config(username="u", password="p", server_name="s"), True),
        (Config(baseurl="u", token="t"), False),  # direct token reaches one server only
    ],
)
def test_can_switch_servers(cfg, expected):
    assert cfg.can_switch_servers() is expected


def test_launcher_finds_a_free_port_when_the_default_is_busy():
    import socket

    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        busy = taken.getsockname()[1]
        taken.listen(1)
        assert free_port("127.0.0.1", busy) != busy


def test_launcher_defaults_to_localhost_only():
    """Binding all interfaces would expose a Plex token to the network."""
    assert build_parser().parse_args([]).host == "127.0.0.1"
