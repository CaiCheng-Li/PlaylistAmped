import argparse
import csv

import pytest
from rich.console import Console

from playlistamp import cli
from playlistamp.config import Config
from playlistamp.matcher import AUTO, MISSING, MatchResult
from playlistamp.normalize import make_track
from playlistamp.plex_index import PlexIndex, PlexTrack
from playlistamp.report import (
    matched_keys,
    print_mapping,
    slugify,
    summarize,
    write_csv,
    write_wanted,
)
from playlistamp.sources import Playlist, SourceTrack
from tests.test_sync import FakePlex


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


@pytest.fixture
def results():
    hit = MatchResult(yt=yt("Song One", ["Band"], 200, "a"), status=AUTO)
    hit.chosen = plex_track("11", "Song One", "Band")
    miss = MatchResult(yt=yt("Song Two", ["Other"], 300, "b"), status=MISSING)
    return [hit, miss]


def test_matched_keys_are_in_playlist_order(results):
    assert matched_keys(results) == ["11"]


def test_summarize_counts_each_status(results, capsys):
    counts = summarize(results, Console())
    assert counts == {AUTO: 1, MISSING: 1}


def test_csv_has_one_row_per_track(results, tmp_path):
    path = write_csv(results, tmp_path / "r.csv")
    with path.open(encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2
    assert rows[0]["plex_rating_key"] == "11"
    assert rows[1]["status"] == "not in library"


def test_wanted_list_holds_only_the_gaps(results, tmp_path):
    path = write_wanted(results, tmp_path / "w.txt")
    assert path.read_text(encoding="utf-8").strip() == "Song Two — Other"


def test_wanted_list_is_skipped_when_nothing_is_missing(results, tmp_path):
    assert write_wanted(results[:1], tmp_path / "w.txt") is None


def test_slugify_survives_awkward_titles():
    assert slugify("Rock & Roll: The 70's!") == "rock-roll-the-70s"
    assert slugify("///") == "playlist"


def test_sync_command_end_to_end(tmp_path, monkeypatch):
    """The whole pipeline with the network and server stubbed out."""
    playlist = Playlist(id="PL1", title="Test Mix", author="me", url="http://yt/PL1")
    playlist.tracks = [yt("Song One", ["Band"], 200, "a"), yt("Nothing I Own", ["Nobody"], 300, "b")]

    index = PlexIndex(
        section="Music", machine="m", updated_at=0, tracks=[plex_track("11", "Song One", "Band")]
    )
    plex = FakePlex()

    monkeypatch.setattr(cli, "fetch_playlist", lambda *a, **k: playlist)
    monkeypatch.setattr(cli, "connect", lambda cfg: plex)
    monkeypatch.setattr(cli, "load_index", lambda *a, **k: index)
    monkeypatch.setattr(cli.config_mod, "load", lambda: Config(baseurl="u", token="t"))
    monkeypatch.setattr(cli, "load_decisions", dict)

    args = argparse.Namespace(
        url="PL1", name=None, section=None, auto_accept=None, review_floor=None,
        yes=True, dry_run=False, no_reorder=False, refresh_index=False,
        refresh_playlist=False, report_dir=tmp_path,
    )
    assert cli.cmd_sync(Console(), args) == 0

    assert plex.created == ["Test Mix"]
    created = plex.playlists()[0]
    assert [str(i.ratingKey) for i in created.items()] == ["11"]
    assert "http://yt/PL1" in created.summary

    assert len(list(tmp_path.glob("report-test-mix-*.csv"))) == 1
    wanted = list(tmp_path.glob("wanted-test-mix-*.txt"))
    assert "Nothing I Own" in wanted[0].read_text(encoding="utf-8")


def test_dry_run_command_touches_nothing(tmp_path, monkeypatch):
    playlist = Playlist(id="PL1", title="Test Mix", author="me", url="http://yt/PL1")
    playlist.tracks = [yt("Song One", ["Band"], 200, "a")]
    index = PlexIndex(
        section="Music", machine="m", updated_at=0, tracks=[plex_track("11", "Song One", "Band")]
    )
    plex = FakePlex()

    monkeypatch.setattr(cli, "fetch_playlist", lambda *a, **k: playlist)
    monkeypatch.setattr(cli, "connect", lambda cfg: plex)
    monkeypatch.setattr(cli, "load_index", lambda *a, **k: index)
    monkeypatch.setattr(cli.config_mod, "load", lambda: Config(baseurl="u", token="t"))
    monkeypatch.setattr(cli, "load_decisions", dict)

    args = argparse.Namespace(
        url="PL1", name=None, section=None, auto_accept=None, review_floor=None,
        yes=False, dry_run=True, no_reorder=False, refresh_index=False,
        refresh_playlist=False, report_dir=tmp_path,
    )
    assert cli.cmd_sync(Console(), args) == 0
    assert plex.created == []


def test_mapping_table_names_the_actual_source(results):
    """The dry-run column must not say YouTube for a Spotify playlist."""
    console = Console(record=True, width=200)
    print_mapping(results, console, "Spotify")
    assert "Spotify" in console.export_text()
