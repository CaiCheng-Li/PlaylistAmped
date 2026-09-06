import pytest

from playlistamp.matcher import AUTO, MISSING, REVIEW, Matcher, score_pair
from playlistamp.normalize import interpretations, make_track
from playlistamp.plex_index import PlexIndex, PlexTrack
from playlistamp.sources import SourceTrack

LIBRARY = [
    ("1", "Bohemian Rhapsody", "Queen", "A Night at the Opera", 354),
    ("2", "Hotel California", "Eagles", "Hotel California", 391),
    ("3", "Hotel California - Live", "Eagles", "Hell Freezes Over", 428),
    ("4", "Blinding Lights", "The Weeknd", "After Hours", 201),
    ("5", "Levels", "Avicii", "True", 202),
    ("6", "Levels (Skrillex Remix)", "Avicii", "Levels Remixes", 245),
    ("7", "Yesterday", "The Beatles", "Help!", 125),
    ("8", "Yesterday", "Boyz II Men", "Covers", 190),
    ("9", "Earfquake", "Tyler, The Creator", "IGOR", 190),
]


def plex_track(key, title, artist, album, duration):
    return PlexTrack.from_dict(
        {
            "rating_key": key,
            "title": title,
            "artist": artist,
            "album_artist": artist,
            "album": album,
            "duration_s": duration,
        }
    )


@pytest.fixture
def index():
    return PlexIndex(
        section="Music",
        machine="test",
        updated_at=0,
        tracks=[plex_track(*row) for row in LIBRARY],
    )


def yt(title, artists, album="", duration=None, track_id="v"):
    return SourceTrack(
        track_id=track_id,
        title=title,
        artists=artists,
        album=album,
        duration_s=duration,
        normalized=make_track(title=title, artists=artists, album=album, duration_s=duration),
    )


@pytest.fixture
def matcher(index):
    return Matcher(index, auto_accept=88.0, review_floor=65.0)


def test_clean_match_is_automatic(matcher):
    result = matcher.match_all([yt("Bohemian Rhapsody (Official Video)", ["Queen"], duration=354)])[0]
    assert result.status == AUTO
    assert result.chosen.rating_key == "1"


def test_channel_suffix_does_not_block_a_match(matcher):
    result = matcher.match_all([yt("Blinding Lights", ["The Weeknd - Topic"], duration=201)])[0]
    assert result.status == AUTO
    assert result.chosen.rating_key == "4"


def test_studio_version_wins_over_live(matcher):
    """The single most important case: a studio track must not land on the live cut."""
    result = matcher.match_all([yt("Hotel California", ["Eagles"], duration=391)])[0]
    assert result.chosen.rating_key == "2"


def test_live_request_prefers_the_live_recording(matcher):
    result = matcher.match_all([yt("Hotel California (Live)", ["Eagles"], duration=428)])[0]
    assert result.chosen.rating_key == "3"


def test_remix_does_not_auto_match_the_original(matcher):
    ranked = matcher.rank(yt("Levels (Skrillex Remix)", ["Avicii"], duration=245))
    assert ranked[0].track.rating_key == "6"


def test_original_does_not_match_the_remix(matcher):
    result = matcher.match_all([yt("Levels", ["Avicii"], duration=202)])[0]
    assert result.chosen.rating_key == "5"


def test_artist_disambiguates_identical_titles(matcher):
    result = matcher.match_all([yt("Yesterday", ["The Beatles"], duration=125)])[0]
    assert result.chosen.rating_key == "7"


def test_wrong_artist_is_not_matched(matcher):
    """A perfect title with an unrelated artist must not auto-accept."""
    result = matcher.match_all([yt("Bohemian Rhapsody", ["Panic! At The Disco"], duration=354)])[0]
    assert result.status != AUTO


def test_absent_track_is_reported_missing(matcher):
    result = matcher.match_all([yt("Some Song Nobody Owns", ["Nobody"], duration=200)])[0]
    assert result.status == MISSING
    assert result.chosen is None


def test_comma_in_artist_name_still_matches(matcher):
    result = matcher.match_all([yt("Earfquake", ["Tyler, The Creator"], duration=190)])[0]
    assert result.chosen.rating_key == "9"


def test_duplicate_guard_does_not_reuse_one_file(matcher):
    """Two different YouTube entries must not collapse onto the same Plex track."""
    results = matcher.match_all(
        [
            yt("Yesterday", ["The Beatles"], duration=125, track_id="a"),
            yt("Yesterday", ["Boyz II Men"], duration=190, track_id="b"),
        ]
    )
    keys = [r.chosen.rating_key for r in results if r.chosen]
    assert len(keys) == len(set(keys))


def test_repeated_track_id_may_reuse_the_same_track(matcher):
    """A playlist that genuinely lists a song twice should map both entries."""
    track = yt("Bohemian Rhapsody", ["Queen"], duration=354, track_id="same")
    results = matcher.match_all([track, track])
    assert [r.chosen.rating_key for r in results] == ["1", "1"]


def test_duration_breaks_a_tie():
    """Same title and artist, different length: the closer duration must win."""
    short = make_track(title="Song", artists=["Band"], album="A", duration_s=200)
    long = make_track(title="Song", artists=["Band"], album="A", duration_s=400)
    query = make_track(title="Song", artists=["Band"], album="A", duration_s=201)
    assert score_pair(query, short)[0] > score_pair(query, long)[0]


def test_variant_mismatch_is_penalised():
    studio = make_track(title="Song", artists=["Band"], album="A", duration_s=200)
    live = make_track(title="Song (Live)", artists=["Band"], album="A", duration_s=200)
    assert score_pair(studio, studio)[0] > score_pair(studio, live)[0]


def test_empty_library_reports_everything_missing():
    empty = PlexIndex(section="Music", machine="test", updated_at=0, tracks=[])
    matcher = Matcher(empty, auto_accept=88.0, review_floor=65.0)
    results = matcher.match_all([yt("Anything", ["Anyone"])])
    assert results[0].status == MISSING


# Entries as they actually appear in user-curated playlists: the "artist" is
# the uploading channel and the real artist is embedded in the title.
def yt_upload(title, uploader, duration=None, track_id="v"):
    forms = interpretations(title=title, artists=[uploader], album="", duration_s=duration)
    return SourceTrack(
        track_id=track_id,
        title=title,
        artists=[uploader],
        album="",
        duration_s=duration,
        normalized=forms[0],
        alternates=forms[1:],
    )


def test_artist_before_the_dash_is_found(matcher):
    result = matcher.match_all([yt_upload("Queen - Bohemian Rhapsody", "SomeUploader", 354)])[0]
    assert result.chosen is not None and result.chosen.rating_key == "1"


def test_artist_after_the_dash_is_found(matcher):
    """'Kashmir - Led Zeppelin' puts the artist on the right of the dash."""
    result = matcher.match_all([yt_upload("Hotel California - Eagles", "OzWho", 391)])[0]
    assert result.chosen is not None and result.chosen.rating_key == "2"


def test_uploader_channel_does_not_defeat_the_match(matcher):
    result = matcher.match_all([yt_upload("Eagles - Hotel California", "beto90diego", 391)])[0]
    assert result.chosen is not None and result.chosen.rating_key == "2"


def test_dash_reading_does_not_break_a_remaster_suffix(matcher):
    """'X - Remastered 2011' must not be read as artist 'X'."""
    result = matcher.match_all([yt("Hotel California - Remastered 2011", ["Eagles"], duration=391)])[0]
    assert result.chosen is not None and result.chosen.rating_key == "2"


# Regression cases from a real 1129-track playlist, where 54% of tracks landed
# in review because a shared word scored as a near-match.
def test_shared_word_is_not_a_match(matcher):
    """'Sky High' must not match 'High'. WRatio scored this pairing 90."""
    result = matcher.match_all([yt("Levels Sky High", ["Elektronomia"], duration=196)])[0]
    assert result.status == MISSING


def test_wrong_artist_sinks_a_perfect_title(matcher):
    """A title that matches exactly but by another artist must not reach review."""
    result = matcher.match_all([yt("Yesterday", ["Some Bedroom Producer"], duration=125)])[0]
    assert result.status == MISSING


def test_placeholder_artist_does_not_count_against_a_match():
    """A compilation tagged 'Various Artists' has no artist, not a wrong one."""
    comp = PlexIndex(
        section="Music", machine="t", updated_at=0,
        tracks=[plex_track("1", "Party Rock Anthem", "Various Artists", "Party Hits", 262)],
    )
    matcher = Matcher(comp, auto_accept=88.0, review_floor=65.0)
    result = matcher.match_all([yt("Party Rock Anthem", ["LMFAO"], duration=262)])[0]
    # Review rather than auto is right here: nothing corroborates the artist.
    assert result.status != MISSING
    assert result.candidates[0].track.rating_key == "1"
