import pytest

from playlistamped.decisions import apply_decisions, remember
from playlistamped.matcher import REMEMBERED, REVIEW, SKIPPED, MatchResult
from playlistamped.plex_index import PlexIndex
from tests.test_report import plex_track, yt


@pytest.mark.parametrize(
    "machine,section,expected",
    [("home", "Music", REMEMBERED), ("other", "Music", REVIEW), ("home", "Other", REVIEW)],
)
def test_saved_match_belongs_to_its_server_and_library(machine, section, expected):
    track = plex_track("22", "Song", "Band")
    original = PlexIndex(section="Music", machine="home", updated_at=0, tracks=[track])
    decisions = {}
    remember(decisions, "source-id", "22", track.display, index=original)
    target = PlexIndex(section=section, machine=machine, updated_at=0, tracks=[track])
    result = MatchResult(yt=yt("Song", ["Band"], track_id="source-id"), status=REVIEW)
    apply_decisions([result], decisions, target)
    assert result.status == expected
    assert result.chosen == (track if expected == REMEMBERED else None)


def test_legacy_match_requires_review_but_ignore_survives_server_switch():
    track = plex_track("22", "Different Song", "Other Band")
    index = PlexIndex(section="Music", machine="other", updated_at=0, tracks=[track])
    result = MatchResult(yt=yt("Song", ["Band"], track_id="source-id"), status=REVIEW)
    apply_decisions([result], {"source-id": {"rating_key": "22"}}, index)
    assert result.status == REVIEW
    assert result.chosen is None
    apply_decisions([result], {"source-id": {"rating_key": None}}, index)
    assert result.status == SKIPPED
