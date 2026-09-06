"""Local web UI.

A second front end over the same library the CLI uses. Long operations run on a
worker thread and the page polls, because indexing a large library across a
remote Plex connection takes minutes and must not block the request.
"""

from __future__ import annotations

import threading
import webbrowser
from dataclasses import dataclass, field

from flask import Flask, jsonify, render_template, request

from . import config as config_mod
from .matcher import AUTO, MANUAL, MISSING, REMEMBERED, REVIEW, SKIPPED, MatchResult, Matcher
from .plex_index import PlexError, connect, load_index
from .report import MATCHED, STATUS_LABEL, matched_keys
from .review import load_decisions, remember, save_decisions, apply_decisions
from .sync import build_summary, sync_playlist
from .sources import PlaylistError, fetch_playlist

app = Flask(__name__)


@dataclass
class Job:
    """The state of one sync run, polled by the page."""

    stage: str = "idle"  # idle | playlist | index | match | writing | done | error
    message: str = ""
    error: str = ""
    playlist_title: str = ""
    playlist_url: str = ""
    source_label: str = ""
    source_short: str = ""
    truncated: str = ""
    total: int = 0
    results: list[MatchResult] = field(default_factory=list)
    pending: int = 0  # decisions made since the last write
    written: str = ""


class State:
    """Process-wide singleton. A lock serialises access to the Plex client."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.plex = None
        self.index = None
        self.job = Job()
        self.decisions: dict[str, dict] = {}
        self.pin_login = None
        self.pin_account = None
        self.pending_cfg: config_mod.Config | None = None


state = State()


def _seconds(value: int | None) -> str:
    if not value:
        return "—"
    return f"{value // 60}:{value % 60:02d}"


def _candidate_json(candidate) -> dict:
    track = candidate.track
    return {
        "rating_key": track.rating_key,
        "title": track.title,
        "artist": track.artist,
        "album": track.album,
        "duration": _seconds(track.duration_s),
        "score": round(candidate.score),
        "components": {k: round(v) for k, v in candidate.components.items()},
    }


def _result_json(index: int, result: MatchResult) -> dict:
    yt = result.yt
    return {
        "index": index,
        "track_id": yt.track_id,
        "status": result.status,
        "label": STATUS_LABEL.get(result.status, result.status),
        "title": yt.title,
        "artists": ", ".join(yt.artists) or "unknown artist",
        "album": yt.album,
        "duration": _seconds(yt.duration_s),
        "chosen": (
            {
                "rating_key": result.chosen.rating_key,
                "title": result.chosen.title,
                "artist": result.chosen.artist,
                "album": result.chosen.album,
                "duration": _seconds(result.chosen.duration_s),
            }
            if result.chosen
            else None
        ),
        "candidates": [_candidate_json(c) for c in result.candidates[:5]],
    }


def _counts(results: list[MatchResult]) -> dict[str, int]:
    counts = {"matched": 0, "review": 0, "missing": 0, "skipped": 0}
    for result in results:
        if result.status in MATCHED:
            counts["matched"] += 1
        elif result.status == REVIEW:
            counts["review"] += 1
        elif result.status == SKIPPED:
            counts["skipped"] += 1
        else:
            counts["missing"] += 1
    return counts


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------


@app.get("/")
def index_page():
    return render_template("index.html")


@app.get("/api/state")
def api_state():
    cfg = config_mod.load()
    return jsonify(
        {
            "configured": cfg.has_connection(),
            "server": cfg.server_name or cfg.baseurl,
            "section": cfg.section,
        }
    )


# --------------------------------------------------------------------------
# Setup
# --------------------------------------------------------------------------


@app.post("/api/setup/pin")
def api_setup_pin():
    """Start a plex.tv link, returning the code for the user to enter."""
    from plexapi.myplex import MyPlexPinLogin

    try:
        state.pin_login = MyPlexPinLogin()
        state.pin_login.run(timeout=300)
    except Exception as exc:
        return jsonify({"error": f"Could not start sign-in: {exc}"}), 500
    return jsonify({"pin": state.pin_login.pin})


@app.get("/api/setup/pin/poll")
def api_setup_pin_poll():
    """Has the code been entered yet? Returns the account's servers once it has."""
    from plexapi.myplex import MyPlexAccount

    login = state.pin_login
    if login is None:
        return jsonify({"error": "No sign-in in progress"}), 400
    if not login.checkLogin():
        if login.expired:
            return jsonify({"error": "The code expired. Start again."}), 408
        return jsonify({"linked": False})

    try:
        account = MyPlexAccount(token=login.token)
        servers = [r for r in account.resources() if "server" in (r.provides or "")]
    except Exception as exc:
        return jsonify({"error": f"Signed in, but could not list servers: {exc}"}), 500

    state.pending_cfg = config_mod.load()
    state.pending_cfg.account_token = login.token
    return jsonify(
        {
            "linked": True,
            "username": account.username,
            "servers": [{"name": r.name, "owned": bool(r.owned)} for r in servers],
        }
    )


@app.post("/api/setup/manual")
def api_setup_manual():
    """Configure with a direct address and token instead of signing in."""
    data = request.get_json(force=True)
    cfg = config_mod.load()
    cfg.baseurl = (data.get("baseurl") or "").strip()
    cfg.token = (data.get("token") or "").strip()
    cfg.account_token = ""
    cfg.server_name = ""
    if not cfg.baseurl or not cfg.token:
        return jsonify({"error": "Both a server address and a token are required."}), 400

    try:
        plex = connect(cfg)
    except PlexError as exc:
        return jsonify({"error": str(exc)}), 400

    state.pending_cfg = cfg
    return jsonify({"sections": _music_sections(plex), "server": plex.friendlyName})


@app.post("/api/setup/server")
def api_setup_server():
    """Pick which of the account's servers to use, and list its music libraries."""
    data = request.get_json(force=True)
    cfg = state.pending_cfg or config_mod.load()
    cfg.server_name = data.get("server_name") or ""
    try:
        plex = connect(cfg)
    except PlexError as exc:
        return jsonify({"error": str(exc)}), 400

    # Cache whichever address answered so later runs skip discovery.
    cfg.baseurl = plex._baseurl
    cfg.token = plex._token
    state.pending_cfg = cfg
    return jsonify({"sections": _music_sections(plex), "server": plex.friendlyName})


def _music_sections(plex) -> list[str]:
    return [s.title for s in plex.library.sections() if s.type == "artist"]


@app.post("/api/setup/finish")
def api_setup_finish():
    data = request.get_json(force=True)
    cfg = state.pending_cfg or config_mod.load()
    cfg.section = data.get("section") or cfg.section
    config_mod.save(cfg)
    state.pending_cfg = None
    state.index = None  # the section may have changed
    state.plex = None
    return jsonify({"ok": True, "server": cfg.server_name or cfg.baseurl, "section": cfg.section})


# --------------------------------------------------------------------------
# Sync
# --------------------------------------------------------------------------


def _run_sync(url: str, name: str, refresh_index: bool, refresh_playlist: bool = False) -> None:
    job = state.job
    cfg = config_mod.load()
    try:
        job.stage, job.message = "playlist", "Reading the playlist…"
        playlist = fetch_playlist(url, refresh=refresh_playlist)
        job.playlist_title = name or playlist.title
        job.playlist_url = playlist.url
        job.source_label = playlist.source_label
        job.source_short = playlist.source_short
        job.total = len(playlist.tracks)
        if playlist.is_truncated:
            # Never sync a partial playlist silently.
            job.truncated = (
                f"Only {len(playlist.tracks)} of {playlist.total_reported} tracks "
                f"could be read from Spotify. {playlist.truncated_note}"
            )

        job.stage = "index"
        job.message = "Reading your Plex library — the first run can take a few minutes…"
        if state.plex is None:
            state.plex = connect(cfg)
        if state.index is None or refresh_index:
            state.index = load_index(state.plex, cfg, refresh=refresh_index)
        job.message = f"{len(state.index)} tracks in your library"

        job.stage, job.message = "match", f"Matching {len(playlist.tracks)} tracks…"
        matcher = Matcher(
            state.index, auto_accept=cfg.auto_accept, review_floor=cfg.review_floor
        )
        results = matcher.match_all(playlist.tracks)

        state.decisions = load_decisions()
        apply_decisions(results, state.decisions, state.index)
        job.results = results

        job.stage, job.message = "writing", "Writing the playlist to Plex…"
        keys = matched_keys(results)
        outcome = sync_playlist(
            state.plex,
            job.playlist_title,
            keys,
            summary=build_summary(playlist.url, len(keys), len(results)),
        )
        job.written = outcome.action
        job.pending = 0
        job.stage, job.message = "done", f"{outcome.action} with {outcome.total} track(s)"
    except (PlaylistError, PlexError) as exc:
        job.stage, job.error = "error", str(exc)
    except Exception as exc:  # a crash here must not leave the page spinning
        job.stage, job.error = "error", f"Unexpected error: {exc}"


@app.post("/api/sync")
def api_sync():
    if state.job.stage in {"playlist", "index", "match", "writing"}:
        return jsonify({"error": "A sync is already running."}), 409

    data = request.get_json(force=True)
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "Paste a YouTube Music or Spotify playlist link first."}), 400

    state.job = Job(stage="playlist", message="Starting…")
    thread = threading.Thread(
        target=_run_sync,
        args=(
            url,
            (data.get("name") or "").strip(),
            bool(data.get("refresh_index")),
            bool(data.get("refresh_playlist")),
        ),
        daemon=True,
    )
    thread.start()
    return jsonify({"ok": True})


@app.get("/api/job")
def api_job():
    job = state.job
    payload = {
        "stage": job.stage,
        "message": job.message,
        "error": job.error,
        "playlist_title": job.playlist_title,
        "playlist_url": job.playlist_url,
        "source_label": job.source_label,
        "source_short": job.source_short,
        "truncated": job.truncated,
        "total": job.total,
        "pending": job.pending,
        "written": job.written,
    }
    if job.stage == "done":
        payload["counts"] = _counts(job.results)
        payload["review"] = [
            _result_json(i, r) for i, r in enumerate(job.results) if r.status == REVIEW
        ]
        payload["matched"] = [
            _result_json(i, r) for i, r in enumerate(job.results) if r.status in MATCHED
        ]
        payload["missing"] = [
            _result_json(i, r)
            for i, r in enumerate(job.results)
            if r.status in (MISSING, SKIPPED)
        ]
    return jsonify(payload)


@app.post("/api/decide")
def api_decide():
    """Record an Add or Ignore for one reviewed track."""
    data = request.get_json(force=True)
    idx = data.get("index")
    action = data.get("action")
    results = state.job.results

    if not isinstance(idx, int) or not 0 <= idx < len(results):
        return jsonify({"error": "Unknown track"}), 400

    result = results[idx]
    if action == "add":
        rating_key = data.get("rating_key")
        candidate = next(
            (c for c in result.candidates if c.track.rating_key == rating_key),
            result.candidates[0] if result.candidates else None,
        )
        if candidate is None:
            return jsonify({"error": "Nothing to add"}), 400
        result.status = MANUAL
        result.chosen = candidate.track
        result.note = "added in review"
        remember(state.decisions, result.yt.track_id, candidate.track.rating_key, candidate.track.display)
    elif action == "ignore":
        result.status = SKIPPED
        result.chosen = None
        result.note = "ignored in review"
        remember(state.decisions, result.yt.track_id, None, "ignored")
    else:
        return jsonify({"error": "Unknown action"}), 400

    save_decisions(state.decisions)
    state.job.pending += 1
    return jsonify({"ok": True, "pending": state.job.pending, "counts": _counts(results)})


@app.post("/api/apply")
def api_apply():
    """Write the accumulated review decisions into the Plex playlist, in order."""
    job = state.job
    if not job.results or state.plex is None:
        return jsonify({"error": "Nothing to apply"}), 400

    keys = matched_keys(job.results)
    try:
        outcome = sync_playlist(
            state.plex,
            job.playlist_title,
            keys,
            summary=build_summary(job.playlist_url, len(keys), len(job.results)),
        )
    except Exception as exc:
        return jsonify({"error": f"Could not update the playlist: {exc}"}), 500

    job.pending = 0
    job.written = outcome.action
    return jsonify({"ok": True, "total": outcome.total, "action": outcome.action})


def serve(host: str = "127.0.0.1", port: int = 5000, open_browser: bool = True) -> None:
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(f"http://{host}:{port}")).start()
    app.run(host=host, port=port, threaded=True)
