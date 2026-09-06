import csv
import io

import pytest

from playlistamped.matcher import AUTO, MISSING, REVIEW, MatchResult
from playlistamped.normalize import make_track
from playlistamped.plex_index import PlexIndex, PlexTrack
from playlistamped.report import csv_bytes, matched_keys, slugify, wanted_bytes
from playlistamped.sources import Playlist, SourceTrack


def yt(title, artists, duration=None, track_id="v"):
    return SourceTrack(
        track_id=track_id,
        title=title,
        artists=artists,
        album="",
        duration_s=duration,
        normalized=make_track(title=title, artists=artists, album="", duration_s=duration),
    )


def plex_track(key, title, artist):
    return PlexTrack.from_dict(
        {
            "rating_key": key,
            "title": title,
            "artist": artist,
            "album_artist": artist,
            "album": "An Album",
            "duration_s": 200,
        }
    )


def make_index(*tracks):
    return PlexIndex(section="Music", machine="m", updated_at=0, tracks=list(tracks))


def make_playlist(title="Test Mix", tracks=()):
    playlist = Playlist(id="PL1", title=title, author="me", url="http://source/PL1")
    playlist.tracks = list(tracks)
    return playlist


@pytest.fixture
def results():
    hit = MatchResult(yt=yt("Song One", ["Band"], 200, "a"), status=AUTO)
    hit.chosen = plex_track("11", "Song One", "Band")
    miss = MatchResult(yt=yt("Song Two", ["Other"], 300, "b"), status=MISSING)
    return [hit, miss]


def test_matched_keys_are_in_playlist_order(results):
    assert matched_keys(results) == ["11"]


def test_csv_has_one_row_per_track(results):
    rows = list(csv.DictReader(io.StringIO(csv_bytes(results).decode("utf-8-sig"))))
    assert len(rows) == 2
    assert rows[0]["plex_rating_key"] == "11"
    assert rows[1]["status"] == "not in library"


def test_csv_carries_the_byte_order_mark_for_spreadsheets(results):
    """Without the BOM, a spreadsheet on Windows mangles accented titles."""
    assert csv_bytes(results).startswith(b"\xef\xbb\xbf")


def test_csv_survives_awkward_characters():
    track = MatchResult(yt=yt('Song, "quoted" — Beyoncé', ["Bandé"], 200, "x"), status=MISSING)
    rows = list(csv.DictReader(io.StringIO(csv_bytes([track]).decode("utf-8-sig"))))
    assert rows[0]["source_title"] == 'Song, "quoted" — Beyoncé'


def test_wanted_list_holds_only_the_gaps(results):
    assert wanted_bytes(results).decode("utf-8").strip() == "Song Two — Other"


def test_wanted_list_includes_unreviewed_tracks():
    """Anything still needing a look is also something you may not own."""
    pending = MatchResult(yt=yt("Unsure", ["Band"], 200, "c"), status=REVIEW)
    assert "Unsure" in wanted_bytes([pending]).decode("utf-8")


def test_slugify_survives_awkward_titles():
    assert slugify("Rock & Roll: The 70's!") == "rock-roll-the-70s"
    assert slugify("///") == "playlist"
