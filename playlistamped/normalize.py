"""Text normalization for track matching.

Pure functions, no I/O. YouTube Music and Plex describe the same recording very
differently -- ``Song (Official Music Video)`` versus ``Song - Remastered 2011``
-- and the whole quality of the matcher rests on reducing both to a comparable
core while *keeping* the distinctions that actually matter (a live cut is not
the studio cut).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Segments that say nothing about which recording this is.
NOISE = re.compile(
    r"""^(
          (official\s+)?(music\s+)?video
        | official\s+(audio|lyrics?|lyric\s+video|visuali[sz]er)
        | lyrics?(\s+video)?
        | audio | visuali[sz]er | m\s*v
        | hd | hq | 4k | 1080p | 720p
        | explicit | clean(\s+version)?
        | (hd\s+|digital\s+|\d{4}\s+)?remaster(ed)?(\s+\d{4})?(\s+version)?
        | from\s+.+
        | deluxe(\s+edition)?
        | bonus\s+track
        | album\s+version
        | single\s+version
        | original\s+mix
        | radio\s+edit
        | stereo | mono
        | full\s+song
    )$""",
    re.VERBOSE,
)

# Segments naming a guest performer.
FEATURE = re.compile(r"^(feat\.?|ft\.?|featuring|w/|with)\s+(?P<who>.+)$", re.IGNORECASE)

# Inline "feat." inside the bare title, e.g. "Song feat. Someone".
INLINE_FEATURE = re.compile(r"\s+(?:feat\.?|ft\.?|featuring)\s+(?P<who>.+)$", re.IGNORECASE)

# Segments marking a genuinely different recording.
VARIANTS: list[tuple[str, re.Pattern[str]]] = [
    ("live", re.compile(r"\b(live|ao\s+vivo|en\s+vivo|en\s+directo|en\s+concierto)\b", re.I)),
    ("acoustic", re.compile(r"\bacoustic\b", re.I)),
    ("unplugged", re.compile(r"\bunplugged\b", re.I)),
    ("demo", re.compile(r"\bdemo\b", re.I)),
    ("remix", re.compile(r"\b(re)?mix\b", re.I)),
    ("instrumental", re.compile(r"\binstrumental\b", re.I)),
    ("karaoke", re.compile(r"\bkaraoke\b", re.I)),
    ("cover", re.compile(r"\bcover\b", re.I)),
    ("reprise", re.compile(r"\breprise\b", re.I)),
    ("speed", re.compile(r"\b(sped\s*up|slowed|nightcore)\b", re.I)),
]

# YouTube channel decorations that are not part of an artist name.
ARTIST_NOISE = re.compile(
    r"\s*[-–—]\s*topic$|\s*vevo$|\s*official$|\s*\(official\)$", re.IGNORECASE
)

ARTIST_SPLIT = re.compile(r"\s*(?:,|&|\+|/|\bx\b|\band\b|\bvs\.?\b)\s*", re.IGNORECASE)

# Placeholder credits carry no identity. Treating them as a real name makes a
# compilation look like a *conflicting* artist rather than an unknown one,
# which wrongly sinks otherwise good matches.
PLACEHOLDER_ARTISTS = {
    "various artists",
    "various",
    "va",
    "unknown",
    "unknown artist",
    "various interprets",
    "soundtrack",
    "original soundtrack",
}

DASH_SPLIT = re.compile(r"\s+[-–—]\s+")

# Noise that trails a title without any brackets around it, e.g.
# "Bon Jovi - It's my life w/ lyrics".
TRAILING_NOISE = re.compile(
    r"\s+(w/\s*lyrics|with\s+lyrics|lyrics|official\s+(music\s+)?video"
    r"|official\s+audio|music\s+video|hd|hq|4k)\s*$",
    re.IGNORECASE,
)

_SEGMENT = re.compile(r"[(\[]([^)\]]*)[)\]]")
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_WS = re.compile(r"\s+")


def strip_accents(text: str) -> str:
    """Fold accented characters onto their ASCII base (Beyoncé -> Beyonce)."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def basic_clean(text: str) -> str:
    """Casefold, de-accent, drop punctuation, collapse whitespace."""
    if not text:
        return ""
    out = strip_accents(text).casefold()
    out = out.replace("&", " and ")
    out = _PUNCT.sub(" ", out)
    return _WS.sub(" ", out).strip()


def clean_artist(name: str) -> str:
    """Strip YouTube channel decorations, then normalize."""
    if not name:
        return ""
    return basic_clean(ARTIST_NOISE.sub("", name.strip()))


def artist_key(name: str) -> str:
    """A stricter artist form for exact bucketing; drops a leading 'the'."""
    return re.sub(r"^the\s+", "", clean_artist(name))


def split_artists(value: str | None) -> list[str]:
    """Split a combined artist string into individual normalized names.

    ``"Calvin Harris & Dua Lipa"`` -> ``["calvin harris", "dua lipa"]``. If
    splitting yields nothing usable the whole string is kept, so names that
    legitimately contain a separator are not destroyed.
    """
    if not value:
        return []
    parts = [p for p in ARTIST_SPLIT.split(value) if p and p.strip()]
    cleaned = [c for c in (clean_artist(p) for p in parts) if c]
    if cleaned:
        return cleaned
    whole = clean_artist(value)
    return [whole] if whole else []


def _classify(segment: str) -> tuple[str, object]:
    """Label one segment: noise, feature, variant, or unknown."""
    probe = basic_clean(segment)
    if not probe:
        return "noise", None
    if NOISE.match(probe):
        return "noise", None
    feat = FEATURE.match(segment.strip())
    if feat:
        return "feature", split_artists(feat.group("who"))
    tags = {tag for tag, pattern in VARIANTS if pattern.search(segment)}
    if tags:
        return "variant", tags
    return "unknown", None


@dataclass(frozen=True)
class NormalizedTitle:
    core: str
    features: frozenset[str]
    variants: frozenset[str]


def normalize_title(title: str) -> NormalizedTitle:
    """Reduce a track title to a comparable core plus feature/variant tags.

    Bracketed segments and a trailing ``- ...`` clause are classified. Noise is
    dropped, guests move to ``features``, and recording variants become tags.
    Unrecognized segments stay in the core -- dropping them could silently merge
    two genuinely different songs.
    """
    if not title:
        return NormalizedTitle("", frozenset(), frozenset())

    features: set[str] = set()
    variants: set[str] = set()
    keep: list[str] = []

    def take(segment: str) -> None:
        kind, payload = _classify(segment)
        if kind == "feature":
            features.update(payload)  # type: ignore[arg-type]
        elif kind == "variant":
            variants.update(payload)  # type: ignore[arg-type]
            # Keep the words: "(Deadmau5 Remix)" must not collide with another remix.
            keep.append(segment)
        elif kind == "unknown":
            keep.append(segment)

    for segment in _SEGMENT.findall(title):
        take(segment)
    head = _SEGMENT.sub(" ", title)

    # A trailing " - ..." clause is a segment only when we recognize it;
    # otherwise it is probably part of the real title ("Live and Let Die").
    parts = DASH_SPLIT.split(head)
    if len(parts) > 1:
        kind, _ = _classify(parts[-1])
        if kind in {"noise", "feature", "variant"}:
            take(parts[-1])
            head = " - ".join(parts[:-1])

    inline = INLINE_FEATURE.search(head)
    if inline:
        features.update(split_artists(inline.group("who")))
        head = head[: inline.start()]

    # Peel unbracketed trailing noise, which can stack ("... official video HD").
    while True:
        stripped = TRAILING_NOISE.sub("", head)
        if stripped == head:
            break
        head = stripped

    pieces = [basic_clean(head)] + [basic_clean(s) for s in keep]
    core = _WS.sub(" ", " ".join(p for p in pieces if p)).strip()
    return NormalizedTitle(core, frozenset(features), frozenset(variants))


@dataclass(frozen=True)
class NormalizedTrack:
    """A track from either source, reduced to comparable form.

    Raw fields are kept alongside so the UI and reports can show the user
    something they will actually recognize.
    """

    title: str
    artists: tuple[str, ...]
    album: str
    features: frozenset[str]
    variants: frozenset[str]
    duration_s: int | None
    title_raw: str
    artists_raw: tuple[str, ...]
    album_raw: str

    @property
    def primary_artist(self) -> str:
        return self.artists[0] if self.artists else ""

    @property
    def all_artists(self) -> frozenset[str]:
        """Credited artists and guests together -- either can carry the match."""
        return frozenset(self.artists) | self.features


def make_track(
    *,
    title: str,
    artists: list[str] | None,
    album: str | None,
    duration_s: int | None,
) -> NormalizedTrack:
    """Build a :class:`NormalizedTrack` from raw source fields."""
    parsed = normalize_title(title or "")
    raw_artists = tuple(a for a in (artists or []) if a)

    expanded: list[str] = []
    for name in raw_artists:
        # Keep the unsplit form alongside the split parts. "Calvin Harris &
        # Dua Lipa" is two artists but "Tyler, The Creator" is one, and the
        # string alone cannot tell us which -- so offer both spellings and let
        # the matcher score whichever actually corresponds to the Plex tag.
        whole = clean_artist(name)
        parts = split_artists(name)
        if whole and whole not in parts:
            expanded.append(whole)
        expanded.extend(parts)
    # Drop placeholders so they read as "no artist known", not a wrong artist.
    expanded = [a for a in expanded if a not in PLACEHOLDER_ARTISTS]
    seen: set[str] = set()
    unique = tuple(a for a in expanded if not (a in seen or seen.add(a)))

    return NormalizedTrack(
        title=parsed.core,
        artists=unique,
        album=normalize_title(album).core if album else "",
        features=parsed.features,
        variants=parsed.variants,
        duration_s=duration_s,
        title_raw=title or "",
        artists_raw=raw_artists,
        album_raw=album or "",
    )


def interpretations(
    *,
    title: str,
    artists: list[str] | None,
    album: str | None,
    duration_s: int | None,
) -> list[NormalizedTrack]:
    """Every plausible reading of one source entry, best guess first.

    User-curated playlists are full of uploads where the credited "artist" is
    really the uploading channel and the actual artist sits in the title --
    ``"Steppenwolf - Born To Be Wild"`` by ``"Max Shkiv"``, or the other way
    round in ``"Kashmir - Led Zeppelin"``. Rather than guess which side is the
    artist, emit both splits and let the matcher score them against the real
    library; a wrong reading simply scores low.
    """
    primary = make_track(title=title, artists=artists, album=album, duration_s=duration_s)
    forms = [primary]

    # Split on the dash only after brackets are gone, so "(Live)" cannot confuse it.
    head = _SEGMENT.sub(" ", title or "")
    parts = [p.strip() for p in DASH_SPLIT.split(head) if p.strip()]
    if len(parts) != 2:
        return forms

    # If either side is a recognised clause ("- Remastered 2011") this is not an
    # artist/title split at all.
    if any(_classify(part)[0] in {"noise", "variant", "feature"} for part in parts):
        return forms

    left, right = parts
    for artist_part, title_part in ((left, right), (right, left)):
        alt = make_track(
            title=title_part, artists=[artist_part], album=album, duration_s=duration_s
        )
        if alt.title and alt.artists and alt.title != primary.title:
            forms.append(alt)
    return forms
