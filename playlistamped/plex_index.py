"""Connect to Plex and keep a local, cached index of the music library.

The whole library is pulled down once and matched locally. Doing it per track
against the server would be thousands of round trips *and* would hand match
quality to Plex's opaque search ranking; a local index is both faster and
something we can score deliberately.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

from plexapi.exceptions import NotFound
from plexapi.myplex import MyPlexAccount
from plexapi.server import PlexServer

from .config import Config, cache_dir
from .normalize import NormalizedTrack, make_track

# Plex pages internally; a larger container means fewer round trips on big libraries.
CONTAINER_SIZE = 500

# The metadata endpoint accepts comma-separated keys, but URLs have limits.
RESOLVE_BATCH = 100

# A cached plex.direct address goes stale when Plex rotates it, and connecting
# to the dead host can hang indefinitely rather than refusing. Without an
# explicit timeout the CLI and the UI both just spin, so bound the direct
# attempt tightly and let rediscovery -- which tries every published
# connection -- have longer.
DIRECT_TIMEOUT = 15
DISCOVER_TIMEOUT = 30


class PlexError(RuntimeError):
    """Connecting to Plex or finding the music section failed."""


@dataclass
class PlexTrack:
    rating_key: str
    title: str
    artist: str
    album_artist: str
    album: str
    duration_s: int | None
    normalized: NormalizedTrack

    @property
    def display(self) -> str:
        return f"{self.title} — {self.artist} · {self.album}"

    def to_dict(self) -> dict:
        return {
            "rating_key": self.rating_key,
            "title": self.title,
            "artist": self.artist,
            "album_artist": self.album_artist,
            "album": self.album,
            "duration_s": self.duration_s,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PlexTrack":
        # Both artist spellings are offered to the matcher: compilations and
        # guest spots are credited on the track, not the album.
        artists = [a for a in {data["artist"], data["album_artist"]} if a]
        return cls(
            rating_key=data["rating_key"],
            title=data["title"],
            artist=data["artist"],
            album_artist=data["album_artist"],
            album=data["album"],
            duration_s=data["duration_s"],
            normalized=make_track(
                title=data["title"],
                artists=artists,
                album=data["album"],
                duration_s=data["duration_s"],
            ),
        )


@dataclass
class PlexIndex:
    section: str
    machine: str
    updated_at: int
    tracks: list[PlexTrack]

    def __len__(self) -> int:
        return len(self.tracks)


def account_for(cfg: Config) -> MyPlexAccount:
    """Sign in to plex.tv with whichever credential is configured."""
    if cfg.account_token:
        return MyPlexAccount(token=cfg.account_token)
    if cfg.username and cfg.password:
        return MyPlexAccount(cfg.username, cfg.password)
    raise PlexError("Not signed in to plex.tv. Connect from the app first.")


def discover(cfg: Config) -> PlexServer:
    """Let plex.tv resolve the server's address and connect to it.

    This is what makes a remote server usable without knowing its IP: the
    account knows every published connection for the server, local and remote,
    and plexapi picks whichever actually answers.
    """
    account = account_for(cfg)
    try:
        resource = account.resource(cfg.server_name)
    except Exception as exc:
        names = ", ".join(
            r.name for r in account.resources() if "server" in (r.provides or "")
        )
        raise PlexError(
            f"No server named {cfg.server_name!r} on this account. Available: {names or 'none'}"
        ) from exc
    try:
        return resource.connect(timeout=DISCOVER_TIMEOUT)
    except Exception as exc:
        raise PlexError(
            f"Could not reach server {cfg.server_name!r}: {exc}. "
            f"Is it online, and is remote access enabled?"
        ) from exc


def connect(cfg: Config) -> PlexServer:
    """Connect to Plex, preferring the cached direct address.

    The remote address plex.tv hands out can change, so a failure on the cached
    one falls back to rediscovery rather than giving up.
    """
    if not cfg.has_connection():
        raise PlexError("No Plex connection configured. Connect from the app first.")

    can_rediscover = bool(cfg.server_name) and bool(
        cfg.account_token or (cfg.username and cfg.password)
    )

    if cfg.baseurl and cfg.token:
        try:
            return PlexServer(cfg.baseurl, cfg.token, timeout=DIRECT_TIMEOUT)
        except Exception as exc:
            if not can_rediscover:
                raise PlexError(f"Could not reach Plex at {cfg.baseurl}: {exc}") from exc

    return discover(cfg)


def music_section(plex: PlexServer, name: str):
    try:
        section = plex.library.section(name)
    except NotFound as exc:
        available = ", ".join(s.title for s in plex.library.sections()) or "none"
        raise PlexError(f"No library section named {name!r}. Available: {available}") from exc
    if section.type != "artist":
        raise PlexError(f"Section {name!r} is a {section.type} library, not music")
    return section


def _cache_file(machine: str, section_key: str | int) -> Path:
    return cache_dir() / "index" / f"{machine}-{section_key}.json"


def _harvest(section) -> Iterator[dict]:
    for track in section.searchTracks(container_size=CONTAINER_SIZE):
        duration = getattr(track, "duration", None)
        yield {
            "rating_key": str(track.ratingKey),
            "title": track.title or "",
            # originalTitle is the track-level artist; it is what rescues
            # compilations and guest features where the album artist differs.
            "artist": getattr(track, "originalTitle", None) or track.grandparentTitle or "",
            "album_artist": track.grandparentTitle or "",
            "album": track.parentTitle or "",
            "duration_s": round(duration / 1000) if duration else None,
        }


def load_index(plex: PlexServer, cfg: Config, *, refresh: bool = False, on_progress=None) -> PlexIndex:
    """Return the library index, rebuilding it only when stale or forced.

    The cache is keyed by server and section and invalidated by the section's
    ``updatedAt``, so an unchanged library costs one cheap metadata call.
    """
    section = music_section(plex, cfg.section)
    machine = plex.machineIdentifier
    updated_at = int(getattr(section, "updatedAt", None).timestamp()) if getattr(
        section, "updatedAt", None
    ) else 0
    path = _cache_file(machine, section.key)

    if path.exists() and not refresh:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("updated_at") == updated_at and data.get("section") == cfg.section:
                return PlexIndex(
                    section=cfg.section,
                    machine=machine,
                    updated_at=updated_at,
                    tracks=[PlexTrack.from_dict(t) for t in data["tracks"]],
                )
        except (OSError, json.JSONDecodeError, KeyError):
            pass  # rebuild

    if on_progress:
        on_progress("Fetching the Plex music library (first run, or library changed)…")

    rows = list(_harvest(section))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"section": cfg.section, "machine": machine, "updated_at": updated_at, "tracks": rows},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return PlexIndex(
        section=cfg.section,
        machine=machine,
        updated_at=updated_at,
        tracks=[PlexTrack.from_dict(r) for r in rows],
    )


def resolve_tracks(plex: PlexServer, rating_keys: Iterable[str]) -> list:
    """Turn cached rating keys back into plexapi objects, batched.

    Playlist creation needs real objects; fetching them one at a time would be
    one request per track. Plex's metadata endpoint takes comma-separated keys,
    so this is a handful of requests instead.
    """
    keys = [str(k) for k in rating_keys]
    if not keys:
        return []

    found: dict[str, object] = {}
    for start in range(0, len(keys), RESOLVE_BATCH):
        batch = keys[start : start + RESOLVE_BATCH]
        try:
            for item in plex.fetchItems(f"/library/metadata/{','.join(batch)}"):
                found[str(item.ratingKey)] = item
        except Exception:
            # A stale key in the batch fails the whole request; fall back to
            # one-by-one so a single deleted track cannot break the sync.
            for key in batch:
                try:
                    item = plex.fetchItem(int(key))
                except Exception:
                    continue
                found[str(item.ratingKey)] = item

    # Preserve the caller's order -- it is the playlist order.
    return [found[k] for k in keys if k in found]
