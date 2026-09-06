"""Downloadable reports: the full mapping as CSV, and a plain wanted list."""

from __future__ import annotations

import csv
import io
import re

from .matcher import AUTO, MANUAL, MISSING, REMEMBERED, REVIEW, SKIPPED, MatchResult

MATCHED = (AUTO, MANUAL, REMEMBERED)

STATUS_LABEL = {
    AUTO: "matched",
    REMEMBERED: "matched (remembered)",
    MANUAL: "matched (reviewed)",
    REVIEW: "needs review",
    SKIPPED: "ignored",
    MISSING: "not in library",
}


def slugify(text: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", text).strip().lower()
    slug = re.sub(r"[\s_-]+", "-", slug)
    return slug or "playlist"


def matched_keys(results: list[MatchResult]) -> list[str]:
    """Rating keys of everything that resolved, in playlist order."""
    return [r.chosen.rating_key for r in results if r.status in MATCHED and r.chosen]


def csv_bytes(results: list[MatchResult]) -> bytes:
    """The whole mapping, one row per source track.

    Encoded utf-8-sig so a spreadsheet opens accented titles correctly.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "position", "status", "source_title", "source_artists", "source_album",
            "source_duration_s", "track_id", "plex_title", "plex_artist", "plex_album",
            "plex_rating_key", "score", "title_score", "artist_score", "album_score",
            "duration_score", "variant_penalty", "note",
        ]
    )
    for i, result in enumerate(results, start=1):
        top = result.candidates[0] if result.candidates else None
        chosen = result.chosen
        parts = top.components if top else {}
        writer.writerow(
            [
                i,
                STATUS_LABEL.get(result.status, result.status),
                result.yt.title,
                "; ".join(result.yt.artists),
                result.yt.album,
                result.yt.duration_s or "",
                result.yt.track_id,
                chosen.title if chosen else "",
                chosen.artist if chosen else "",
                chosen.album if chosen else "",
                chosen.rating_key if chosen else "",
                f"{result.score:.1f}" if top else "",
                f"{parts.get('title', 0):.0f}" if parts else "",
                f"{parts.get('artist', 0):.0f}" if parts else "",
                f"{parts.get('album', 0):.0f}" if parts else "",
                f"{parts.get('duration', 0):.0f}" if parts else "",
                f"{parts.get('penalty', 0):.0f}" if parts else "",
                result.note,
            ]
        )
    return buffer.getvalue().encode("utf-8-sig")


def wanted_bytes(results: list[MatchResult]) -> bytes:
    """The tracks your library does not have, as a plain shopping list."""
    wanted = [r for r in results if r.status in (MISSING, REVIEW)]
    lines = [
        f"{r.yt.title} — {', '.join(r.yt.artists) or 'unknown artist'}" for r in wanted
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")
