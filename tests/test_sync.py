"""Sync is the only stage that mutates the user's server, so its diff logic is
exercised here against stand-ins rather than a live Plex install."""

import pytest

from playlistamped.sync import find_playlist, sync_playlist


class FakeItem:
    def __init__(self, rating_key):
        self.ratingKey = int(rating_key)

    def __repr__(self):
        return f"<item {self.ratingKey}>"


class FakePlaylist:
    def __init__(self, title, items):
        self.title = title
        self.playlistType = "audio"
        self._items = list(items)
        self.summary = ""
        self.calls = []

    def items(self):
        return list(self._items)

    def addItems(self, items):
        self.calls.append(("add", [i.ratingKey for i in items]))
        self._items.extend(items)

    def removeItems(self, items):
        self.calls.append(("remove", [i.ratingKey for i in items]))
        drop = {id(i) for i in items}
        self._items = [i for i in self._items if id(i) not in drop]

    def editSummary(self, summary):
        self.summary = summary


class FakePlex:
    def __init__(self, playlists=()):
        self._playlists = list(playlists)
        self.created = []

    def playlists(self):
        return list(self._playlists)

    def createPlaylist(self, title, items=None, **kwargs):
        playlist = FakePlaylist(title, items or [])
        self._playlists.append(playlist)
        self.created.append(title)
        return playlist

    def fetchItems(self, path):
        keys = path.rsplit("/", 1)[-1].split(",")
        return [FakeItem(k) for k in keys]


def keys_of(playlist):
    return [str(i.ratingKey) for i in playlist.items()]


def test_creates_a_playlist_when_none_exists():
    plex = FakePlex()
    outcome = sync_playlist(plex, "Road Trip", ["1", "2", "3"], summary="s")
    assert outcome.action == "created"
    assert outcome.total == 3
    assert plex.created == ["Road Trip"]


def test_existing_playlist_in_the_right_order_is_left_alone():
    existing = FakePlaylist("Road Trip", [FakeItem(1), FakeItem(2)])
    plex = FakePlex([existing])
    outcome = sync_playlist(plex, "Road Trip", ["1", "2"])
    assert outcome.action == "unchanged"
    assert existing.calls == []


def test_lookup_is_case_insensitive():
    existing = FakePlaylist("Road Trip", [FakeItem(1)])
    plex = FakePlex([existing])
    assert find_playlist(plex, "road trip") is existing


def test_reorder_rebuilds_in_place_and_keeps_the_playlist():
    """The playlist object is reused, so Plexamp keeps its artwork and place."""
    existing = FakePlaylist("Road Trip", [FakeItem(2), FakeItem(1)])
    plex = FakePlex([existing])
    outcome = sync_playlist(plex, "Road Trip", ["1", "2"])
    assert outcome.action == "updated" and outcome.reordered
    assert keys_of(existing) == ["1", "2"]
    assert plex.created == []


def test_reorder_adds_before_removing_so_the_playlist_is_never_empty():
    """An empty playlist can be garbage-collected by Plex mid-update."""
    existing = FakePlaylist("Road Trip", [FakeItem(2), FakeItem(1)])
    plex = FakePlex([existing])
    sync_playlist(plex, "Road Trip", ["1", "2"])
    assert [call[0] for call in existing.calls] == ["add", "remove"]


def test_no_reorder_updates_content_but_keeps_order():
    existing = FakePlaylist("Road Trip", [FakeItem(3), FakeItem(1)])
    plex = FakePlex([existing])
    outcome = sync_playlist(plex, "Road Trip", ["1", "2"], reorder=False)
    assert outcome.action == "updated"
    assert outcome.added == 1 and outcome.removed == 1
    # 3 dropped, 2 appended; 1 stays where the user had it.
    assert keys_of(existing) == ["1", "2"]


def test_no_reorder_leaves_a_shuffled_but_equal_playlist_untouched():
    existing = FakePlaylist("Road Trip", [FakeItem(2), FakeItem(1)])
    plex = FakePlex([existing])
    outcome = sync_playlist(plex, "Road Trip", ["1", "2"], reorder=False)
    assert outcome.action == "unchanged"
    assert keys_of(existing) == ["2", "1"]


def test_dry_run_writes_nothing():
    existing = FakePlaylist("Road Trip", [FakeItem(9)])
    plex = FakePlex([existing])
    outcome = sync_playlist(plex, "Road Trip", ["1", "2"], dry_run=True)
    assert outcome.action == "dry-run"
    assert existing.calls == [] and plex.created == []


def test_no_matches_does_not_create_an_empty_playlist():
    plex = FakePlex()
    outcome = sync_playlist(plex, "Road Trip", [])
    assert outcome.action == "unchanged"
    assert plex.created == []


def test_summary_records_provenance():
    plex = FakePlex()
    sync_playlist(plex, "Road Trip", ["1"], summary="from youtube")
    assert plex.playlists()[0].summary == "from youtube"
