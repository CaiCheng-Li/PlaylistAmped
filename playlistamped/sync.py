"""Create or update the Plex playlist that Plexamp will show."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from plexapi.server import PlexServer

from .plex_index import resolve_tracks


@dataclass
class SyncOutcome:
    action: str  # created | updated | unchanged | dry-run
    title: str
    added: int = 0
    removed: int = 0
    reordered: bool = False
    total: int = 0
    missing_keys: list[str] = field(default_factory=list)


def find_playlist(plex: PlexServer, title: str):
    """Find an existing audio playlist by title, case-insensitively."""
    wanted = title.strip().casefold()
    for playlist in plex.playlists():
        if playlist.title.strip().casefold() == wanted:
            if getattr(playlist, "playlistType", "audio") == "audio":
                return playlist
    return None


def build_summary(source_url: str, matched: int, total: int) -> str:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"Mirrored from {source_url} — {matched}/{total} tracks matched. Last synced {stamp}."


def _set_summary(playlist, summary: str) -> None:
    """Best effort: older Plex servers reject the summary field on playlists."""
    try:
        playlist.editSummary(summary)
    except Exception:
        pass


def sync_playlist(
    plex: PlexServer,
    title: str,
    rating_keys: list[str],
    *,
    summary: str = "",
    reorder: bool = True,
    dry_run: bool = False,
) -> SyncOutcome:
    """Make the Plex playlist named ``title`` hold exactly ``rating_keys``, in order.

    An existing playlist is updated in place rather than replaced, so it keeps
    its rating key -- and with it the artwork and position Plexamp shows.
    """
    items = resolve_tracks(plex, rating_keys)
    resolved = {str(i.ratingKey) for i in items}
    missing = [k for k in rating_keys if k not in resolved]

    existing = find_playlist(plex, title)

    if dry_run:
        return SyncOutcome(
            action="dry-run",
            title=title,
            total=len(items),
            missing_keys=missing,
        )

    if not items:
        return SyncOutcome(action="unchanged", title=title, total=0, missing_keys=missing)

    if existing is None:
        playlist = plex.createPlaylist(title, items=items)
        if summary:
            _set_summary(playlist, summary)
        return SyncOutcome(
            action="created", title=title, added=len(items), total=len(items), missing_keys=missing
        )

    # Capture the current items first: each carries the playlistItemID needed
    # to remove exactly that entry.
    current = existing.items()
    current_keys = [str(i.ratingKey) for i in current]
    desired_keys = [str(i.ratingKey) for i in items]

    if current_keys == desired_keys:
        if summary:
            _set_summary(existing, summary)
        return SyncOutcome(action="unchanged", title=title, total=len(items), missing_keys=missing)

    same_set = set(current_keys) == set(desired_keys)

    if same_set and not reorder:
        if summary:
            _set_summary(existing, summary)
        return SyncOutcome(action="unchanged", title=title, total=len(items), missing_keys=missing)

    if not reorder:
        # Content-only update: add what is new, drop what is gone, leave the
        # user's hand-curated ordering alone.
        to_add = [i for i in items if str(i.ratingKey) not in set(current_keys)]
        to_remove = [i for i in current if str(i.ratingKey) not in set(desired_keys)]
        if to_add:
            existing.addItems(to_add)
        if to_remove:
            existing.removeItems(to_remove)
        if summary:
            _set_summary(existing, summary)
        return SyncOutcome(
            action="updated",
            title=title,
            added=len(to_add),
            removed=len(to_remove),
            total=len(items),
            missing_keys=missing,
        )

    # Full reorder: append the desired list, then remove the original entries.
    # Adding before removing keeps the playlist non-empty throughout, so Plex
    # never garbage-collects it mid-update, and the survivors end up in order.
    existing.addItems(items)
    if current:
        existing.removeItems(current)
    if summary:
        _set_summary(existing, summary)

    added = len([k for k in desired_keys if k not in set(current_keys)])
    removed = len([k for k in current_keys if k not in set(desired_keys)])
    return SyncOutcome(
        action="updated",
        title=title,
        added=added,
        removed=removed,
        reordered=True,
        total=len(items),
        missing_keys=missing,
    )
