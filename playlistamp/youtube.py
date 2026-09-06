"""Read a public YouTube Music playlist.

``ytmusicapi`` serves public and unlisted playlists with no authentication at
all, so this stage needs no setup from the user. ``auth_file`` is plumbed
through unused: passing a ``ytmusicapi browser`` credential file is the entire
change needed to reach private playlists and Liked Music later.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ytmusicapi import YTMusic

from .config import cache_dir
from .normalize import interpretations
from .sources import YOUTUBE, Playlist, PlaylistError, SourceTrack

LIST_PARAM = re.compile(r"[?&]list=([A-Za-z0-9_-]+)")
BARE_ID = re.compile(r"^[A-Za-z0-9_-]+$")

# ytmusicapi sometimes leaves view counts or a duration in the artists array.
NOT_AN_ARTIST = re.compile(
    r"^\s*(\d[\d.,]*\s*[KMB]?\s*(views|plays)|\d+:\d{2}(:\d{2})?)\s*$", re.I
)


def extract_playlist_id(value: str) -> str:
    """Accept a full URL or a bare id and return the playlist id.

    Handles ``music.youtube.com`` and ``youtube.com`` URLs, and strips the
    ``VL`` prefix that YouTube's own browse ids carry.
    """
    value = (value or "").strip()
    if not value:
        raise PlaylistError("No playlist URL or id given")

    found = LIST_PARAM.search(value)
    playlist_id = found.group(1) if found else value
    if not found and not BARE_ID.match(playlist_id):
        raise PlaylistError(f"Could not find a playlist id in {value!r}")
    if playlist_id.startswith("VL"):
        playlist_id = playlist_id[2:]
    return playlist_id


def _artist_names(entry: dict) -> list[str]:
    names: list[str] = []
    for artist in entry.get("artists") or []:
        name = (artist or {}).get("name")
        if not name or NOT_AN_ARTIST.match(name):
            continue
        names.append(name)
    return names


def _cache_file(playlist_id: str) -> Path:
    return cache_dir() / "youtube" / f"{playlist_id}.json"


def fetch_raw(playlist_id: str, *, auth_file: str | None = None, refresh: bool = False) -> dict:
    """Fetch the playlist payload, caching it so re-runs and debugging are cheap."""
    path = _cache_file(playlist_id)
    if path.exists() and not refresh:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass  # fall through and refetch

    client = YTMusic(auth_file) if auth_file else YTMusic()
    try:
        # limit=None is essential: the default stops at 100 tracks, silently.
        raw = client.get_playlist(playlist_id, limit=None)
    except Exception as exc:  # ytmusicapi raises a variety of types
        # ytmusicapi errors can carry the entire API response; keep a usable
        # fragment rather than dumping it over the user's terminal.
        detail = " ".join(str(exc).split())[:160]
        raise PlaylistError(
            f"Could not read playlist {playlist_id!r} — check the id, and that "
            f"the playlist is public or unlisted. Private playlists and Liked "
            f"Music are not supported yet.\n[dim]{detail}[/dim]"
        ) from exc

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return raw


def parse(raw: dict, playlist_id: str) -> Playlist:
    """Turn the ytmusicapi payload into normalized tracks."""
    author = raw.get("author")
    author_name = author.get("name", "") if isinstance(author, dict) else (author or "")

    playlist = Playlist(
        id=playlist_id,
        title=raw.get("title") or f"YouTube playlist {playlist_id}",
        author=author_name,
        url=f"https://music.youtube.com/playlist?list={playlist_id}",
        source=YOUTUBE,
    )

    for entry in raw.get("tracks") or []:
        title = entry.get("title") or ""
        if not title:
            continue
        # Deleted or region-blocked entries carry no usable metadata.
        if entry.get("isAvailable") is False:
            playlist.unavailable.append(title)
            continue

        album = entry.get("album")
        album_name = album.get("name", "") if isinstance(album, dict) else (album or "")
        artists = _artist_names(entry)
        duration = entry.get("duration_seconds")
        duration_s = int(duration) if duration else None

        forms = interpretations(
            title=title, artists=artists, album=album_name, duration_s=duration_s
        )
        playlist.tracks.append(
            SourceTrack(
                track_id=entry.get("videoId") or "",
                title=title,
                artists=artists,
                album=album_name,
                duration_s=duration_s,
                normalized=forms[0],
                alternates=forms[1:],
            )
        )
    # Deliberately no total_reported: limit=None returns the whole playlist, so
    # there is nothing to truncate. Unavailable entries are missing from the
    # source, not missing from our read of it.
    return playlist


def fetch_playlist(
    url_or_id: str, *, auth_file: str | None = None, refresh: bool = False
) -> Playlist:
    """Resolve a URL/id to a fully parsed :class:`Playlist`."""
    playlist_id = extract_playlist_id(url_or_id)
    return parse(fetch_raw(playlist_id, auth_file=auth_file, refresh=refresh), playlist_id)
