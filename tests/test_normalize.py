import pytest

from playlistamped.normalize import (
    artist_key,
    basic_clean,
    clean_artist,
    make_track,
    normalize_title,
    split_artists,
)


@pytest.mark.parametrize(
    "raw,core",
    [
        ("Bohemian Rhapsody (Official Video)", "bohemian rhapsody"),
        ("Bohemian Rhapsody (Official Music Video)", "bohemian rhapsody"),
        ("Take Five [HD]", "take five"),
        ("Smells Like Teen Spirit (Lyrics)", "smells like teen spirit"),
        ("Hotel California - Remastered 2011", "hotel california"),
        ("Hotel California - 2011 Remaster", "hotel california"),
        ("Come Together (Remastered)", "come together"),
        ("Karma Police (Album Version)", "karma police"),
        ("Titanium (Radio Edit)", "titanium"),
        ("Everlong (Deluxe Edition)", "everlong"),
    ],
)
def test_noise_is_stripped(raw, core):
    assert normalize_title(raw).core == core


@pytest.mark.parametrize(
    "raw,core,features",
    [
        ("Blinding Lights (feat. ROSALIA)", "blinding lights", {"rosalia"}),
        ("Song ft. Someone", "song", {"someone"}),
        ("Song feat. A & B", "song", {"a", "b"}),
        ("Stay (with Justin Bieber)", "stay", {"justin bieber"}),
    ],
)
def test_features_move_out_of_the_title(raw, core, features):
    parsed = normalize_title(raw)
    assert parsed.core == core
    assert parsed.features == features


def test_with_inside_a_real_title_is_not_a_feature():
    """'With' only marks a guest inside a bracketed segment."""
    parsed = normalize_title("Dancing With Myself")
    assert parsed.core == "dancing with myself"
    assert parsed.features == frozenset()


@pytest.mark.parametrize(
    "raw,variants",
    [
        ("Wish You Were Here (Live at Wembley)", {"live"}),
        ("Layla (Acoustic)", {"acoustic"}),
        ("Levels (Skrillex Remix)", {"remix"}),
        ("Blackbird (Demo)", {"demo"}),
        ("Song (Instrumental)", {"instrumental"}),
        ("Garota de Ipanema (Ao Vivo)", {"live"}),
    ],
)
def test_variants_are_tagged_not_discarded(raw, variants):
    assert normalize_title(raw).variants == variants


def test_variant_words_stay_in_the_core_so_remixes_do_not_collide():
    a = normalize_title("Levels (Skrillex Remix)")
    b = normalize_title("Levels (Avicii Remix)")
    assert a.variants == b.variants == {"remix"}
    assert a.core != b.core


def test_live_in_a_real_title_is_not_a_variant():
    """The trailing clause is classified; the title itself is left alone."""
    parsed = normalize_title("Live and Let Die - Remastered 2010")
    assert parsed.core == "live and let die"
    assert parsed.variants == frozenset()


def test_accents_and_case_fold():
    assert basic_clean("Beyoncé — DÉJÀ VU") == "beyonce deja vu"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("The Weeknd - Topic", "the weeknd"),
        ("EminemVEVO", "eminem"),
        ("Radiohead - Topic", "radiohead"),
    ],
)
def test_channel_decorations_are_stripped(raw, expected):
    assert clean_artist(raw) == expected


def test_artist_key_drops_leading_the():
    assert artist_key("The Beatles") == artist_key("Beatles") == "beatles"


def test_split_artists():
    assert split_artists("Calvin Harris & Dua Lipa") == ["calvin harris", "dua lipa"]


def test_ambiguous_artist_keeps_both_spellings():
    """'Tyler, The Creator' is one artist but 'A, B' is two; offer both forms."""
    track = make_track(title="Earfquake", artists=["Tyler, The Creator"], album="", duration_s=190)
    assert "tyler the creator" in track.all_artists
    assert "tyler" in track.all_artists


def test_make_track_folds_channel_and_album_noise():
    track = make_track(
        title="Blinding Lights (feat. ROSALIA)",
        artists=["The Weeknd - Topic"],
        album="After Hours (Deluxe)",
        duration_s=201,
    )
    assert track.title == "blinding lights"
    assert track.artists == ("the weeknd",)
    assert track.album == "after hours"
    assert track.all_artists == {"the weeknd", "rosalia"}


def test_empty_input_is_safe():
    parsed = normalize_title("")
    assert parsed.core == ""
    assert not parsed.features and not parsed.variants


@pytest.mark.parametrize(
    "raw,core",
    [
        ("Bon Jovi - It's my life w/ lyrics", "bon jovi it s my life"),
        ("Song with lyrics", "song"),
        ("Song official video HD", "song"),
    ],
)
def test_unbracketed_trailing_noise_is_peeled(raw, core):
    assert normalize_title(raw).core == core


@pytest.mark.parametrize(
    "raw,core",
    [
        ('Nobody (from "Kaiju No. 8")', "nobody"),
        ("Nobody - From Kaiju No. 8", "nobody"),
        ("I Ain't Worried (From \"Top Gun: Maverick\")", "i ain t worried"),
        ("Bad Day (HD Remaster)", "bad day"),
        ("Song - Digital Remaster Version", "song"),
    ],
)
def test_soundtrack_and_remaster_suffixes_are_noise(raw, core):
    assert normalize_title(raw).core == core


def test_placeholder_artists_are_dropped():
    track = make_track(title="Song", artists=["Various Artists"], album="", duration_s=200)
    assert track.artists == ()
    assert track.all_artists == frozenset()
