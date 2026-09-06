"""Spotify source. Network is stubbed; the payload shapes below are trimmed
copies of what open.spotify.com and the pathfinder endpoint actually return."""

import json

import pytest

from playlistamped import spotify
from playlistamped.sources import SPOTIFY, YOUTUBE, PlaylistError, detect_source

EMBED_HTML = """<html><body>
<script id="__NEXT_DATA__" type="application/json">{json}</script>
</body></html>"""


def embed_page(name="Test Mix", tracks=3, total=3, token="tok123"):
    payload = {
        "props": {
            "pageProps": {
                "state": {
                    "data": {
                        "entity": {
                            "name": name,
                            "type": "playlist",
                            "trackCount": total,
                            "trackList": [
                                {
                                    "title": f"Embed Song {i}",
                                    "subtitle": "Band A, Band B",
                                    "duration": 200000,
                                    "uri": f"spotify:track:embed{i}",
                                    "isPlayable": True,
                                }
                                for i in range(tracks)
                            ],
                        }
                    }
                },
                "config": {"accessToken": token},
            }
        }
    }
    return EMBED_HTML.format(json=json.dumps(payload))


def graphql_page(start, count, total):
    return {
        "data": {
            "playlistV2": {
                "name": "Test Mix",
                "content": {
                    "totalCount": total,
                    "items": [
                        {
                            "itemV2": {
                                "data": {
                                    "name": f"Song {i}",
                                    "uri": f"spotify:track:id{i}",
                                    "artists": {"items": [{"profile": {"name": "Band A"}}]},
                                    "albumOfTrack": {"name": "An Album"},
                                    "trackDuration": {"totalMilliseconds": 210000},
                                }
                            }
                        }
                        for i in range(start, start + count)
                    ],
                },
            }
        }
    }


@pytest.fixture(autouse=True)
def no_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(spotify, "cache_dir", lambda: tmp_path)


@pytest.mark.parametrize(
    "value",
    [
        "https://open.spotify.com/playlist/37i9dQZF1DWXRqgorJj26U",
        "https://open.spotify.com/playlist/37i9dQZF1DWXRqgorJj26U?si=abc",
        "https://open.spotify.com/intl-de/playlist/37i9dQZF1DWXRqgorJj26U",
        "spotify:playlist:37i9dQZF1DWXRqgorJj26U",
        "37i9dQZF1DWXRqgorJj26U",
    ],
)
def test_playlist_id_from_every_link_shape(value):
    assert spotify.extract_playlist_id(value) == "37i9dQZF1DWXRqgorJj26U"


def test_nonsense_link_is_rejected():
    with pytest.raises(PlaylistError):
        spotify.extract_playlist_id("not a playlist")


@pytest.mark.parametrize(
    "value,expected",
    [
        ("https://open.spotify.com/playlist/abc", SPOTIFY),
        ("spotify:playlist:abc", SPOTIFY),
        ("https://music.youtube.com/playlist?list=PL1", YOUTUBE),
        ("PLbareid", YOUTUBE),
    ],
)
def test_source_detection(value, expected):
    assert detect_source(value) == expected


def test_graphql_pages_through_the_whole_playlist(monkeypatch):
    """The embed page alone caps at 100 tracks; GraphQL must page past that."""
    calls = []

    def fake_get(url, headers=None):
        if "open.spotify.com" in url:
            return embed_page(total=250)
        calls.append(url)
        offset = len(calls[:-1]) * 100
        remaining = 250 - offset
        return json.dumps(graphql_page(offset, min(100, remaining), 250))

    monkeypatch.setattr(spotify, "_get", fake_get)
    playlist = spotify.fetch_playlist("spotify:playlist:abc", refresh=True)

    assert len(playlist.tracks) == 250
    assert playlist.total_reported == 250
    assert playlist.is_truncated is False
    assert len(calls) == 3  # 100 + 100 + 50
    assert playlist.tracks[0].title == "Song 0"
    assert playlist.tracks[-1].title == "Song 249"


def test_graphql_gives_albums_and_durations(monkeypatch):
    monkeypatch.setattr(
        spotify,
        "_get",
        lambda url, headers=None: embed_page(total=1)
        if "open.spotify" in url
        else json.dumps(graphql_page(0, 1, 1)),
    )
    track = spotify.fetch_playlist("spotify:playlist:abc", refresh=True).tracks[0]
    assert track.album == "An Album"
    assert track.duration_s == 210
    assert track.artists == ["Band A"]
    assert track.track_id == "id0"


def test_falls_back_to_embed_when_graphql_fails(monkeypatch):
    """A rotated query hash must degrade to 100 tracks, not break entirely."""

    def fake_get(url, headers=None):
        if "open.spotify.com" in url:
            return embed_page(tracks=100, total=250)
        raise RuntimeError("PersistedQueryNotFound")

    monkeypatch.setattr(spotify, "_get", fake_get)
    playlist = spotify.fetch_playlist("spotify:playlist:abc", refresh=True)

    assert len(playlist.tracks) == 100
    assert playlist.total_reported == 250
    assert playlist.is_truncated is True
    assert "first 100 tracks" in playlist.truncated_note


def test_embed_fallback_splits_the_artist_string(monkeypatch):
    """Embed joins artists with commas and non-breaking spaces."""

    def fake_get(url, headers=None):
        if "open.spotify.com" in url:
            return embed_page(tracks=1, total=1)
        raise RuntimeError("no graphql")

    monkeypatch.setattr(spotify, "_get", fake_get)
    track = spotify.fetch_playlist("spotify:playlist:abc", refresh=True).tracks[0]
    assert track.artists == ["Band A", "Band B"]
    assert track.album == ""  # the embed payload carries no album


def test_complete_embed_read_is_not_flagged_truncated(monkeypatch):
    def fake_get(url, headers=None):
        if "open.spotify.com" in url:
            return embed_page(tracks=12, total=12)
        raise RuntimeError("no graphql")

    monkeypatch.setattr(spotify, "_get", fake_get)
    playlist = spotify.fetch_playlist("spotify:playlist:abc", refresh=True)
    assert len(playlist.tracks) == 12
    assert playlist.is_truncated is False
    assert playlist.truncated_note == ""


def test_unreadable_page_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(spotify, "_get", lambda url, headers=None: "<html>nope</html>")
    with pytest.raises(PlaylistError, match="embed page"):
        spotify.fetch_playlist("spotify:playlist:abc", refresh=True)


def test_non_track_items_are_reported_unavailable(monkeypatch):
    page = graphql_page(0, 2, 3)
    page["data"]["playlistV2"]["content"]["items"].append(
        {"itemV2": {"data": {"uri": "spotify:local:x"}}}  # a local file
    )
    page["data"]["playlistV2"]["content"]["totalCount"] = 3

    monkeypatch.setattr(
        spotify,
        "_get",
        lambda url, headers=None: embed_page(total=3)
        if "open.spotify" in url
        else json.dumps(page),
    )
    playlist = spotify.fetch_playlist("spotify:playlist:abc", refresh=True)
    assert len(playlist.tracks) == 2
    assert playlist.unavailable == ["spotify:local:x"]


def test_results_are_cached_between_runs(monkeypatch):
    hits = []

    def fake_get(url, headers=None):
        hits.append(url)
        return embed_page(total=1) if "open.spotify" in url else json.dumps(graphql_page(0, 1, 1))

    monkeypatch.setattr(spotify, "_get", fake_get)
    spotify.fetch_playlist("spotify:playlist:abc", refresh=True)
    before = len(hits)
    spotify.fetch_playlist("spotify:playlist:abc")  # served from cache
    assert len(hits) == before
