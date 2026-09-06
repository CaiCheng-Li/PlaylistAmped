"""Console summary plus CSV / wanted-list reports."""

from __future__ import annotations

import csv
import re
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .matcher import AUTO, MANUAL, MISSING, REMEMBERED, REVIEW, SKIPPED, MatchResult

MATCHED = (AUTO, MANUAL, REMEMBERED)

STATUS_STYLE = {
    AUTO: "green",
    REMEMBERED: "green",
    MANUAL: "cyan",
    REVIEW: "yellow",
    SKIPPED: "dim",
    MISSING: "red",
}

STATUS_LABEL = {
    AUTO: "matched",
    REMEMBERED: "matched (remembered)",
    MANUAL: "matched (reviewed)",
    REVIEW: "needs review",
    SKIPPED: "skipped",
    MISSING: "not in library",
}


def slugify(text: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", text).strip().lower()
    slug = re.sub(r"[\s_-]+", "-", slug)
    return slug or "playlist"


def matched_keys(results: list[MatchResult]) -> list[str]:
    """Rating keys of everything that resolved, in playlist order."""
    return [r.chosen.rating_key for r in results if r.status in MATCHED and r.chosen]


def summarize(results: list[MatchResult], console: Console) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1

    total = len(results)
    matched = sum(counts.get(s, 0) for s in MATCHED)

    table = Table(box=None, pad_edge=False)
    table.add_column("", width=22)
    table.add_column("", justify="right", width=5)

    for status in (AUTO, REMEMBERED, MANUAL, SKIPPED, MISSING, REVIEW):
        if counts.get(status):
            table.add_row(
                f"[{STATUS_STYLE[status]}]{STATUS_LABEL[status]}[/{STATUS_STYLE[status]}]",
                str(counts[status]),
            )
    console.print()
    console.print(table)
    rate = (matched / total * 100) if total else 0.0
    console.print(f"[bold]{matched}/{total}[/bold] tracks matched ([bold]{rate:.0f}%[/bold])")
    return counts


def print_mapping(
    results: list[MatchResult], console: Console, source_label: str = "Source"
) -> None:
    """The full proposed mapping, for --dry-run."""
    table = Table(title="Proposed playlist", title_justify="left", header_style="bold")
    table.add_column("#", justify="right", width=3, style="dim")
    table.add_column(source_label)
    table.add_column("Plex")
    table.add_column("Score", justify="right", width=5)

    for i, result in enumerate(results, start=1):
        style = STATUS_STYLE.get(result.status, "")
        plex = result.chosen.display if result.chosen else f"[{style}]{STATUS_LABEL[result.status]}[/{style}]"
        table.add_row(str(i), result.yt.display, plex, f"{result.score:.0f}" if result.candidates else "")
    console.print(table)


def write_csv(results: list[MatchResult], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig so Excel on Windows opens accented titles correctly.
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "position", "status", "source_title", "source_artists", "source_album",
                "source_duration_s", "track_id", "plex_title", "plex_artist", "plex_album", "plex_rating_key",
                "score", "title_score", "artist_score", "album_score", "duration_score",
                "variant_penalty", "note",
            ]
        )
        for i, result in enumerate(results, start=1):
            top = result.candidates[0] if result.candidates else None
            chosen = result.chosen
            components = top.components if top else {}
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
                    f"{components.get('title', ''):.0f}" if components else "",
                    f"{components.get('artist', ''):.0f}" if components else "",
                    f"{components.get('album', ''):.0f}" if components else "",
                    f"{components.get('duration', ''):.0f}" if components else "",
                    f"{components.get('penalty', 0):.0f}" if components else "",
                    result.note,
                ]
            )
    return path


def write_wanted(results: list[MatchResult], path: Path) -> Path | None:
    """List the tracks your library does not have, for acquisition."""
    wanted = [r for r in results if r.status in (MISSING, REVIEW)]
    if not wanted:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{r.yt.title} — {', '.join(r.yt.artists) or 'unknown artist'}" for r in wanted]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def report_paths(directory: Path, playlist_title: str) -> tuple[Path, Path]:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = slugify(playlist_title)
    return directory / f"report-{slug}-{stamp}.csv", directory / f"wanted-{slug}-{stamp}.txt"
