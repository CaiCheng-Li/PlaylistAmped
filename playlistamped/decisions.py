"""Remembered review decisions.

Add and Ignore choices are keyed by the source's track id across playlists.
Saved matches also record the server and library that own the Plex track id.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .config import cache_dir
from .matcher import MISSING, REMEMBERED, REVIEW, SKIPPED, MatchResult
from .plex_index import PlexIndex

SKIP = None


def decisions_path() -> Path:
    return cache_dir() / "decisions.json"


def load_decisions() -> dict[str, dict]:
    path = decisions_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_decisions(decisions: dict[str, dict]) -> None:
    path = decisions_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(decisions, ensure_ascii=False, indent=2), encoding="utf-8")


def remember(
    decisions: dict[str, dict], track_id: str, rating_key: str | None, label: str,
    *, index: PlexIndex | None = None,
) -> None:
    if not track_id:
        return
    decisions[track_id] = {
        "rating_key": rating_key,
        "label": label,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "machine": index.machine if index is not None else None,
        "section": index.section if index is not None else None,
    }


def forget(decisions: dict[str, dict], track_id: str) -> bool:
    """Drop one remembered decision so the track is asked about again."""
    return decisions.pop(track_id, None) is not None


def apply_decisions(
    results: list[MatchResult], decisions: dict[str, dict], index: PlexIndex
) -> list[MatchResult]:
    """Replay past choices so previously reviewed tracks need no prompting."""
    by_key = {t.rating_key: t for t in index.tracks}

    for result in results:
        if result.status not in (REVIEW, MISSING):
            continue
        decision = decisions.get(result.yt.track_id)
        if decision is None:
            continue

        rating_key = decision.get("rating_key")
        if rating_key is None:
            result.status = SKIPPED
            result.note = "ignored earlier"
        elif (
            decision.get("machine") == index.machine
            and decision.get("section") == index.section
            and rating_key in by_key
        ):
            result.status = REMEMBERED
            result.chosen = by_key[rating_key]
            result.note = "matched earlier"
        # Old choices without a server identity, or choices from another
        # library, need review: rating keys are only unique within a server.
    return results
