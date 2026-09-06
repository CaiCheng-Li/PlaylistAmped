"""Interactive review of borderline matches, with decisions remembered forever.

Every choice is written to a decision file keyed by YouTube video id, so a
track is reviewed once and never asked about again -- including in other
playlists that contain the same song. That is what keeps repeat syncing quiet.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .config import cache_dir
from .matcher import MANUAL, MISSING, REMEMBERED, REVIEW, SKIPPED, MatchResult
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


def remember(decisions: dict[str, dict], track_id: str, rating_key: str | None, label: str) -> None:
    if not track_id:
        return
    decisions[track_id] = {
        "rating_key": rating_key,
        "label": label,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


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
            result.note = "skipped earlier"
        elif rating_key in by_key:
            result.status = REMEMBERED
            result.chosen = by_key[rating_key]
            result.note = "matched earlier"
        # A remembered track that has since left the library falls through and
        # is reviewed again, which is the right outcome.
    return results


def _panel(result: MatchResult) -> Panel:
    yt = result.yt
    length = f"{yt.duration_s // 60}:{yt.duration_s % 60:02d}" if yt.duration_s else "?"
    body = f"[bold]{yt.title}[/bold]\n{', '.join(yt.artists) or 'unknown artist'}"
    if yt.album:
        body += f"\n[dim]{yt.album}[/dim]"
    return Panel(body, title=f"YouTube · {length}", title_align="left", border_style="red")


def _table(result: MatchResult) -> Table:
    table = Table(box=None, pad_edge=False)
    table.add_column("#", style="bold cyan", width=2)
    table.add_column("Score", justify="right", width=5)
    table.add_column("Track")
    table.add_column("Why", style="dim")

    for i, candidate in enumerate(result.candidates[:5], start=1):
        track = candidate.track
        length = (
            f"{track.duration_s // 60}:{track.duration_s % 60:02d}" if track.duration_s else "?"
        )
        table.add_row(
            str(i),
            f"{candidate.score:.0f}",
            f"{track.title} — {track.artist}\n[dim]{track.album} · {length}[/dim]",
            candidate.why(),
        )
    return table


def review(
    results: list[MatchResult], decisions: dict[str, dict], console: Console
) -> list[MatchResult]:
    """Walk the borderline matches with the user. Returns the same list, mutated."""
    pending = [r for r in results if r.status == REVIEW]
    if not pending:
        return results

    if not sys.stdin.isatty():
        console.print(
            f"[yellow]{len(pending)} track(s) need review but this is not an "
            f"interactive terminal — leaving them unmatched.[/yellow]"
        )
        for result in pending:
            result.status = MISSING
            result.note = "needs review"
        return results

    console.print()
    console.print(
        f"[bold]{len(pending)} track(s) need a decision.[/bold] "
        "[dim]1-5 choose · s skip · a accept best for all remaining · q stop reviewing[/dim]"
    )

    accept_rest = False
    for position, result in enumerate(pending, start=1):
        best = result.candidates[0] if result.candidates else None

        if accept_rest:
            if best:
                result.status = MANUAL
                result.chosen = best.track
                result.note = "bulk accepted"
                remember(decisions, result.yt.track_id, best.track.rating_key, best.track.display)
            else:
                result.status = MISSING
            continue

        console.print()
        console.rule(f"[dim]{position}/{len(pending)}[/dim]", align="left")
        console.print(_panel(result))
        if not result.candidates:
            console.print("[dim]No candidates above the floor.[/dim]")
            result.status = MISSING
            continue
        console.print(_table(result))

        choice = console.input("[bold cyan]choice[/bold cyan] [dim](1/s/a/q)[/dim] ").strip().lower()

        if choice == "q":
            console.print("[dim]Stopping review; the rest stay unmatched.[/dim]")
            for remaining in pending[position - 1 :]:
                if remaining.status == REVIEW:
                    remaining.status = MISSING
                    remaining.note = "review stopped"
            break
        if choice == "a":
            accept_rest = True
            if best:
                result.status = MANUAL
                result.chosen = best.track
                result.note = "bulk accepted"
                remember(decisions, result.yt.track_id, best.track.rating_key, best.track.display)
            continue
        if choice == "s":
            result.status = SKIPPED
            result.note = "skipped"
            remember(decisions, result.yt.track_id, SKIP, "skipped")
            continue

        if choice.isdigit() and 1 <= int(choice) <= len(result.candidates[:5]):
            picked = result.candidates[int(choice) - 1]
            result.status = MANUAL
            result.chosen = picked.track
            result.note = "chosen in review"
            remember(decisions, result.yt.track_id, picked.track.rating_key, picked.track.display)
            continue

        # Anything else (including a bare Enter) defers without remembering,
        # so an accidental keypress does not permanently skip a track.
        result.status = MISSING
        result.note = "no decision"

    save_decisions(decisions)
    return results
