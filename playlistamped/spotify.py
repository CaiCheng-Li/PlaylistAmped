"""Read a public Spotify playlist, with no account or app registration.

Spotify's February 2026 changes moved metadata endpoints off the Client
Credentials flow and put Developer Mode behind Premium, so registering an app
is a poor trade for reading a public playlist. Instead this uses what the web
player itself uses:

1. The public embed page carries a short-lived access token.
2. That token works against the pathfinder GraphQL endpoint, which pages
   through the whole playlist and includes album names.
3. If that ever stops working, the embed page's own payload still lists the
   first 100 tracks, and the playlist is flagged as truncated rather than
   silently synced short.

The token is scoped to embed playback: it is refused by api.spotify.com
(``429 QUOTA_EXCEEDED``), which is why the GraphQL endpoint is used instead.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .config import cache_dir
from .normalize import interpretations
from .sources import SPOTIFY, Playlist, PlaylistError, SourceTrack

PLAYLIST_URL = re.compile(
    r"(?:open\.spotify\.com/(?:intl-[a-z]{2}/)?playlist/|spotify:playlist:)([A-Za-z0-9]+)"
)
BARE_ID = re.compile(r"^[A-Za-z0-9]{16,}$")

EMBED = "https://open.spotify.com/embed/playlist/{}"
GRAPHQL = "https://api-partner.spotify.com/pathfinder/v1/query"

# Spotify serves only persisted queries -- a raw query body is refused with
# "Missing extensions in the request" -- so this hash is pinned. Spotify
# rotates these occasionally; when it does, the embed fallback takes over and
# the user is told the playlist was truncated. Updating this one constant
# restores full playlists.
PLAYLIST_QUERY_HASH = "19ff1327c29e99c208c86d7a9d8f1929cfdf3d3202a0ff4253c821f1901aa94d"

PAGE_SIZE = 100
MAX_PAGES = 200  # 20k tracks; a guard against a totalCount that never settles

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_NEXT_DATA = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)
_TOKEN = re.compile(r'"accessToken"\s*:\s*"([^"]+)"')


def extract_playlist_id(value: str) -> str:
    """Accept a Spotify URL, a ``spotify:playlist:`` URI, or a bare id."""
    value = (value or "").strip()
    if not value:
        raise PlaylistError("No playlist link given")

    found = PLAYLIST_URL.search(value)
    if found:
        return found.group(1)
    if BARE_ID.match(value):
        return value
    raise PlaylistError(f"Could not find a Spotify playlist id in {value!r}")


def _get(url: str, headers: dict | None = None) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", "replace")


def _embed_payload(playlist_id: str) -> tuple[str, dict]:
    """Fetch the embed page, returning its access token and entity payload."""
    try:
        html = _get(EMBED.format(playlist_id))
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 400):
            raise PlaylistError(
                f"No public Spotify playlist {playlist_id!r}. Private playlists "
                f"cannot be read; check the link is a public one."
            ) from exc
        raise PlaylistError(f"Could not reach Spotify ({exc.code})") from exc
    except Exception as exc:
        raise PlaylistError(f"Could not reach Spotify: {exc}") from exc

    found = _NEXT_DATA.search(html)
    if not found:
        raise PlaylistError("Spotify's embed page changed shape; cannot read this playlist.")

    data = json.loads(found.group(1))
    entity = (
        data.get("props", {})
        .get("pageProps", {})
        .get("state", {})
        .get("data", {})
        .get("entity", {})
    )
    if not entity:
        raise PlaylistError("That link does not look like a readable public playlist.")

    token_match = _TOKEN.search(html)
    return (token_match.group(1) if token_match else ""), entity


def _graphql_page(playlist_id: str, token: str, offset: int) -> dict:
    variables = json.dumps(
        {"uri": f"spotify:playlist:{playlist_id}", "offset": offset, "limit": PAGE_SIZE}
    )
    extensions = json.dumps(
        {"persistedQuery": {"version": 1, "sha256Hash": PLAYLIST_QUERY_HASH}}
    )
    url = (
        f"{GRAPHQL}?operationName=fetchPlaylist"
        f"&variables={urllib.parse.quote(variables)}"
        f"&extensions={urllib.parse.quote(extensions)}"
    )
    payload = json.loads(_get(url, {"Authorization": f"Bearer {token}"}))
    if payload.get("errors"):
        raise PlaylistError(str(payload["errors"])[:200])
    return payload["data"]["playlistV2"]


def _rows_from_graphql(items: list[dict]) -> tuple[list[dict], list[str]]:
    """Flatten GraphQL items into plain rows, separating what cannot be played."""
    rows: list[dict] = []
    unavailable: list[str] = []
    for item in items:
        data = (item.get("itemV2") or {}).get("data") or {}
        name = data.get("name")
        if not name:
            # Local files, removed tracks and podcast episodes land here.
            unavailable.append(data.get("uri") or "unknown item")
            continue
        artists = [
            a.get("profile", {}).get("name", "")
            for a in (data.get("artists") or {}).get("items", [])
        ]
        duration = (data.get("trackDuration") or {}).get("totalMilliseconds")
        rows.append(
            {
                "id": (data.get("uri") or "").rsplit(":", 1)[-1],
                "title": name,
                "artists": [a for a in artists if a],
                "album": (data.get("albumOfTrack") or {}).get("name") or "",
                "duration_s": round(duration / 1000) if duration else None,
            }
        )
    return rows, unavailable


def _rows_from_embed(entity: dict) -> tuple[list[dict], list[str]]:
    """The fallback payload: the first 100 tracks, without album names."""
    rows: list[dict] = []
    unavailable: list[str] = []
    for item in entity.get("trackList") or []:
        title = item.get("title")
        if not title:
            continue
        if item.get("isPlayable") is False:
            unavailable.append(title)
            continue
        # Artists arrive as one string joined by commas and non-breaking spaces.
        subtitle = (item.get("subtitle") or "").replace("\xa0", " ")
        duration = item.get("duration")
        rows.append(
            {
                "id": (item.get("uri") or "").rsplit(":", 1)[-1] or item.get("uid", ""),
                "title": title,
                "artists": [a.strip() for a in subtitle.split(",") if a.strip()],
                "album": "",
                "duration_s": round(duration / 1000) if duration else None,
            }
        )
    return rows, unavailable


def _cache_file(playlist_id: str) -> Path:
    return cache_dir() / "spotify" / f"{playlist_id}.json"


def fetch_raw(playlist_id: str, *, refresh: bool = False) -> dict:
    """Assemble the whole playlist, preferring GraphQL and falling back to embed."""
    path = _cache_file(playlist_id)
    if path.exists() and not refresh:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass

    token, entity = _embed_payload(playlist_id)
    name = entity.get("name") or f"Spotify playlist {playlist_id}"
    total = entity.get("trackCount")

    rows: list[dict] = []
    unavailable: list[str] = []
    note = ""

    if token:
        try:
            offset = 0
            for _ in range(MAX_PAGES):
                page = _graphql_page(playlist_id, token, offset)
                content = page.get("content") or {}
                total = content.get("totalCount", total)
                page_rows, page_gone = _rows_from_graphql(content.get("items") or [])
                rows.extend(page_rows)
                unavailable.extend(page_gone)

                offset += PAGE_SIZE
                if not content.get("items") or (total and offset >= total):
                    break
        except Exception as exc:  # noqa: BLE001 - any failure falls back
            rows, unavailable = [], []
            note = f"Spotify's full-playlist endpoint refused the request ({exc})."

    if not rows:
        rows, unavailable = _rows_from_embed(entity)
        # The note explains the cause only; callers state the counts, so the
        # two together do not repeat the same numbers back at the user.
        fallback = "Spotify's embed page exposes only the first 100 tracks."
        note = f"{note} {fallback}".strip() if note else fallback

    raw = {
        "id": playlist_id,
        "name": name,
        "total": total,
        "note": note if (total and len(rows) < total) else "",
        "tracks": rows,
        "unavailable": unavailable,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return raw


def parse(raw: dict) -> Playlist:
    playlist = Playlist(
        id=raw["id"],
        title=raw.get("name") or f"Spotify playlist {raw['id']}",
        author="",
        url=f"https://open.spotify.com/playlist/{raw['id']}",
        source=SPOTIFY,
        unavailable=list(raw.get("unavailable") or []),
        total_reported=raw.get("total"),
        truncated_note=raw.get("note") or "",
    )
    for row in raw.get("tracks") or []:
        forms = interpretations(
            title=row["title"],
            artists=row["artists"],
            album=row["album"],
            duration_s=row["duration_s"],
        )
        playlist.tracks.append(
            SourceTrack(
                track_id=row["id"],
                title=row["title"],
                artists=row["artists"],
                album=row["album"],
                duration_s=row["duration_s"],
                normalized=forms[0],
                alternates=forms[1:],
            )
        )
    return playlist


def fetch_playlist(url_or_id: str, *, refresh: bool = False) -> Playlist:
    playlist_id = extract_playlist_id(url_or_id)
    return parse(fetch_raw(playlist_id, refresh=refresh))
