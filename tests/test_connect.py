"""Connection strategy: a cached address is fast, but a remote server's address
can change, so failure there must fall back to plex.tv discovery."""

import pytest

from playlistamp import plex_index
from playlistamp.config import Config
from playlistamp.plex_index import PlexError, connect


class FakeServer:
    def __init__(self, name="via-direct"):
        self.friendlyName = name


def test_no_credentials_at_all_is_a_clear_error():
    with pytest.raises(PlexError, match="playlistamp config"):
        connect(Config())


@pytest.mark.parametrize(
    "cfg,expected",
    [
        (Config(), False),
        (Config(baseurl="u", token="t"), True),
        (Config(account_token="a", server_name="s"), True),
        (Config(username="u", password="p", server_name="s"), True),
        (Config(account_token="a"), False),  # no server named
        (Config(token="t"), False),  # no address
    ],
)
def test_has_connection(cfg, expected):
    assert cfg.has_connection() is expected


def test_direct_address_is_preferred(monkeypatch):
    monkeypatch.setattr(
        plex_index, "PlexServer", lambda url, token, timeout=None: FakeServer("direct")
    )
    monkeypatch.setattr(
        plex_index, "discover", lambda cfg: pytest.fail("should not have discovered")
    )
    server = connect(Config(baseurl="http://x:32400", token="t"))
    assert server.friendlyName == "direct"


def test_account_only_config_discovers(monkeypatch):
    monkeypatch.setattr(plex_index, "discover", lambda cfg: FakeServer("discovered"))
    server = connect(Config(account_token="a", server_name="Home"))
    assert server.friendlyName == "discovered"


def test_stale_address_falls_back_to_discovery(monkeypatch):
    """A remote server's plex.direct address rotates; that must not be fatal."""

    def boom(url, token, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr(plex_index, "PlexServer", boom)
    monkeypatch.setattr(plex_index, "discover", lambda cfg: FakeServer("rediscovered"))

    server = connect(
        Config(baseurl="http://stale:32400", token="t", account_token="a", server_name="Home")
    )
    assert server.friendlyName == "rediscovered"


def test_stale_address_without_account_reports_the_failure(monkeypatch):
    def boom(url, token, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr(plex_index, "PlexServer", boom)
    with pytest.raises(PlexError, match="Could not reach Plex"):
        connect(Config(baseurl="http://stale:32400", token="t"))


def test_direct_attempt_is_bounded_by_a_timeout(monkeypatch):
    """A rotated plex.direct address can hang forever without an explicit
    timeout, leaving the CLI and UI spinning with no way out."""
    seen = {}

    def record(url, token, timeout=None):
        seen["timeout"] = timeout
        return FakeServer("direct")

    monkeypatch.setattr(plex_index, "PlexServer", record)
    connect(Config(baseurl="http://x:32400", token="t"))
    assert seen["timeout"] == plex_index.DIRECT_TIMEOUT
