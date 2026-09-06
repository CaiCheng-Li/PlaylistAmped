"""Match YouTube tracks against the local Plex index.

Two phases. Candidate generation narrows tens of thousands of Plex tracks to a
couple of dozen plausible ones using a fast title pass; scoring then weighs
title, artist, album, duration and recording variant to pick among them.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process

from .normalize import NormalizedTrack, artist_key
from .plex_index import PlexIndex, PlexTrack
from .sources import SourceTrack

# A great title with the wrong artist is a bad match, so artist carries real
# weight. Album is deliberately light: YouTube often reports a single or an
# "EP" name that will not match how the album is tagged in Plex.
WEIGHTS = {"title": 0.55, "artist": 0.30, "album": 0.07, "duration": 0.08}

VARIANT_PENALTY = 18.0  # per mismatched tag (live / acoustic / remix / ...)
VARIANT_PENALTY_CAP = 45.0
FEATURE_BONUS = 3.0

# An artist score this low means a different artist, whatever the title says.
# Applied as a ramped penalty rather than a hard veto, because a compilation
# tagged "Various Artists" can score badly on a match that is genuinely right.
ARTIST_GATE = 60.0
ARTIST_GATE_PENALTY = 30.0

# Generation casts a wide net for recall; scoring below is what decides.
CANDIDATE_LIMIT = 40
TITLE_CUTOFF = 55.0

AUTO = "auto"
REVIEW = "review"
MISSING = "missing"
REMEMBERED = "remembered"
MANUAL = "manual"
SKIPPED = "skipped"


def _duration_score(a: int | None, b: int | None) -> float:
    """Duration is the disambiguator: it separates the album cut from the
    extended mix or the live take when titles alone cannot."""
    if not a or not b:
        return 60.0  # unknown, mildly neutral
    delta = abs(a - b)
    if delta <= 3:
        return 100.0
    if delta >= 30:
        return 0.0
    return 100.0 * (1.0 - (delta - 3) / 27.0)


def _title_score(a: str, b: str) -> float:
    """Length-sensitive title similarity.

    Deliberately *not* ``WRatio`` or ``token_set_ratio``: both reward one title
    merely containing the other, which scores "Sky High" against "High" at 90
    and floods the results with wrong songs that share a word. ``ratio`` and
    ``token_sort_ratio`` both punish the missing words, while the token variant
    still forgives reordering.
    """
    return float(max(fuzz.ratio(a, b), fuzz.token_sort_ratio(a, b)))


def _artist_score(yt: frozenset[str], px: frozenset[str]) -> float:
    if not yt or not px:
        return 50.0
    return max(
        (fuzz.token_set_ratio(a, b) for a in yt for b in px),
        default=0.0,
    )


def _album_score(yt: str, px: str) -> float:
    if not yt or not px:
        return 50.0
    return fuzz.token_set_ratio(yt, px)


def score_pair(yt: NormalizedTrack, px: NormalizedTrack) -> tuple[float, dict[str, float]]:
    """Score one candidate pairing, returning the total and its components."""
    components = {
        "title": _title_score(yt.title, px.title),
        "artist": float(_artist_score(yt.all_artists, px.all_artists)),
        "album": float(_album_score(yt.album, px.album)),
        "duration": _duration_score(yt.duration_s, px.duration_s),
    }
    total = sum(components[k] * w for k, w in WEIGHTS.items())

    # A live or remixed cut is a different recording. Penalise hard enough that
    # it never auto-accepts against the studio version, but leave it visible in
    # review -- sometimes it is the only copy in the library.
    mismatched = yt.variants ^ px.variants
    penalty = min(VARIANT_PENALTY * len(mismatched), VARIANT_PENALTY_CAP)

    # A wrong artist should sink a match however well the titles line up.
    # Weighting alone cannot do this: at 30% weight a 26/100 artist score still
    # leaves a same-word title above the review floor.
    if yt.all_artists and px.all_artists and components["artist"] < ARTIST_GATE:
        shortfall = (ARTIST_GATE - components["artist"]) / ARTIST_GATE
        penalty += ARTIST_GATE_PENALTY * shortfall

    total -= penalty

    # A guest credit that shows up on the Plex side corroborates the match.
    if yt.features and (yt.features & px.all_artists):
        total += FEATURE_BONUS

    components["penalty"] = -penalty
    return max(0.0, min(100.0, total)), components


@dataclass
class Candidate:
    track: PlexTrack
    score: float
    components: dict[str, float]

    def why(self) -> str:
        c = self.components
        return (
            f"title {c['title']:.0f} · artist {c['artist']:.0f} · "
            f"album {c['album']:.0f} · length {c['duration']:.0f}"
            + (f" · variant {c['penalty']:.0f}" if c.get("penalty") else "")
        )


@dataclass
class MatchResult:
    yt: SourceTrack
    status: str
    chosen: PlexTrack | None = None
    candidates: list[Candidate] = field(default_factory=list)
    note: str = ""

    @property
    def score(self) -> float:
        return self.candidates[0].score if self.candidates else 0.0


class Matcher:
    def __init__(self, index: PlexIndex, *, auto_accept: float, review_floor: float) -> None:
        self.tracks = index.tracks
        self.auto_accept = auto_accept
        self.review_floor = review_floor
        self._titles = [t.normalized.title for t in index.tracks]

        # Exact (artist, title) bucket: the common case, answered without fuzz.
        self._exact: dict[tuple[str, str], list[int]] = defaultdict(list)
        for i, track in enumerate(index.tracks):
            for name in track.normalized.all_artists:
                self._exact[(artist_key(name), track.normalized.title)].append(i)

    def _candidate_indexes(self, form: NormalizedTrack) -> set[int]:
        found: set[int] = set()
        for name in form.all_artists:
            found.update(self._exact.get((artist_key(name), form.title), ()))
        for _choice, _score, idx in process.extract(
            form.title,
            self._titles,
            scorer=fuzz.WRatio,
            limit=CANDIDATE_LIMIT,
            score_cutoff=TITLE_CUTOFF,
        ):
            found.add(idx)
        return found

    def rank(self, yt: SourceTrack) -> list[Candidate]:
        """All plausible Plex tracks for one YouTube track, best first.

        Each reading of the source entry (see ``normalize.interpretations``) is
        scored independently and a candidate keeps its best score, so an upload
        whose real artist hides in the title still finds its match.
        """
        best: dict[int, Candidate] = {}
        for form in yt.forms:
            for idx in self._candidate_indexes(form):
                px = self.tracks[idx]
                total, components = score_pair(form, px.normalized)
                if idx not in best or total > best[idx].score:
                    best[idx] = Candidate(track=px, score=total, components=components)

        scored = sorted(best.values(), key=lambda c: c.score, reverse=True)
        return scored

    def match_all(self, tracks: list[SourceTrack]) -> list[MatchResult]:
        """Match a whole playlist, keeping one Plex track per YouTube track.

        The duplicate guard matters because a library often holds the same song
        on an album and a greatest-hits compilation; without it a playlist can
        quietly collapse two different entries onto one file.
        """
        results: list[MatchResult] = []
        claimed: set[str] = set()
        seen_tracks: set[str] = set()

        for yt in tracks:
            ranked = self.rank(yt)
            repeat = yt.track_id in seen_tracks
            seen_tracks.add(yt.track_id)

            available = [c for c in ranked if repeat or c.track.rating_key not in claimed]
            best = available[0] if available else None

            if best is None or best.score < self.review_floor:
                results.append(MatchResult(yt=yt, status=MISSING, candidates=ranked[:5]))
                continue

            if best.score >= self.auto_accept:
                claimed.add(best.track.rating_key)
                runners_up = [c for c in ranked if c is not best][:4]
                results.append(
                    MatchResult(yt=yt, status=AUTO, chosen=best.track, candidates=[best, *runners_up])
                )
            else:
                results.append(MatchResult(yt=yt, status=REVIEW, candidates=available[:5]))
        return results
