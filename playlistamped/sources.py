"""Playlist sources and the types they all produce.

YouTube Music and Spotify describe tracks differently but the matcher only ever
sees a :class:`SourceTrack`, so adding a source means writing one fetcher, not
touching the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .normalize import NormalizedTrack

YOUTUBE = "youtube"
SPOTIFY = "spotify"

SOURCE_LABEL = {YOUTUBE: "YouTube Music", SPOTIFY: "Spotify"}
# Short form for the narrow label column on a review card.
SOURCE_SHORT = {YOUTUBE: "YouTube", SPOTIFY: "Spotify"}


class PlaylistError(RuntimeError):
    """A playlist could not be read (bad link, private, or removed)."""


@dataclass
class SourceTrack:
    """One track as the source describes it, plus its normalized readings."""

    track_id: str
    title: str
    artists: list[str]
    album: str
    duration_s: int | None
    normalized: NormalizedTrack
    # Alternative readings of "Artist - Title" uploads; see normalize.interpretations.
    alternates: list[NormalizedTrack] = field(default_factory=list)

    @property
    def forms(self) -> list[NormalizedTrack]:
        """Every reading the matcher should try, best guess first."""
        return [self.normalized, *self.alternates]

    @property
    def display(self) -> str:
        artists = ", ".join(self.artists) or "unknown artist"
        return f"{self.title} — {artists}"


@dataclass
class Playlist:
    id: str
    title: str
    author: str
    url: str
    source: str = YOUTUBE
    tracks: list[SourceTrack] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)
    # What the source says the playlist holds. When this exceeds the number of
    # tracks actually read, the UI must say so rather than quietly sync a
    # partial playlist.
    total_reported: int | None = None
    truncated_note: str = ""

    @property
    def source_label(self) -> str:
        return SOURCE_LABEL.get(self.source, self.source)

    @property
    def source_short(self) -> str:
        return SOURCE_SHORT.get(self.source, self.source)

    @property
    def is_truncated(self) -> bool:
        return bool(self.total_reported) and len(self.tracks) < self.total_reported


def detect_source(url_or_id: str) -> str:
    """Which service a link belongs to. Bare ids are assumed to be YouTube."""
    probe = (url_or_id or "").strip().lower()
    if "spotify.com" in probe or probe.startswith("spotify:"):
        return SPOTIFY
    return YOUTUBE


def fetch_playlist(url_or_id: str, *, refresh: bool = False) -> Playlist:
    """Read a playlist from whichever service the link points at."""
    # Imported here so the source modules can import this one for its types.
    if detect_source(url_or_id) == SPOTIFY:
        from . import spotify

        return spotify.fetch_playlist(url_or_id, refresh=refresh)

    from . import youtube

    return youtube.fetch_playlist(url_or_id, refresh=refresh)
