"""The application. Everything the program does is reachable from here.

Long operations run on a worker thread and the page polls, because indexing a
large library across a remote Plex connection takes minutes and must not block
a request.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from flask import Flask, Response, jsonify, render_template, request

from . import config as config_mod
from .decisions import apply_decisions, forget, load_decisions, remember, save_decisions
from .matcher import MANUAL, MISSING, REVIEW, SKIPPED, MatchResult, Matcher
from .plex_index import PlexError, connect, load_index, music_section
from .report import MATCHED, STATUS_LABEL, csv_bytes, matched_keys, slugify, wanted_bytes
from .sources import PlaylistError, fetch_playlist
from .sync import build_summary, sync_playlist

app = Flask(__name__)

RUNNING = {"playlist", "index", "match", "writing"}


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
    preview: bool = False


class State:
    """Process-wide singleton; one person on one machine."""

    def __init__(self) -> None:
        self.plex = None
        self.index = None
        self.job = Job()
        self.decisions: dict[str, dict] = {}
        self.pin_login = None
        self.pending_cfg: config_mod.Config | None = None
        self.pending_servers: list[dict] = []

    def drop_connection(self) -> None:
        self.plex = None
        self.index = None
        self.job = Job()


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
    src = result.yt
    return {
        "index": index,
        "track_id": src.track_id,
        "status": result.status,
        "label": STATUS_LABEL.get(result.status, result.status),
        "title": src.title,
        "artists": ", ".join(src.artists) or "unknown artist",
        "album": src.album,
        "duration": _seconds(src.duration_s),
        "note": result.note,
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


def _music_sections(plex) -> list[str]:
    return [s.title for s in plex.library.sections() if s.type == "artist"]


def _fail(message: str, code: int = 400):
    return jsonify({"error": message}), code


def _working_config() -> config_mod.Config:
    """The settings a setup step should build on.

    A half-finished sign-in lives in ``state.pending_cfg`` because the account
    token is not saved until the user picks a server. But an abandoned flow
    leaves that object behind, so it is only trusted before there is a working
    connection; once configured, saved settings always win.
    """
    saved = config_mod.load()
    if saved.has_connection():
        return saved
    return state.pending_cfg or saved


# --------------------------------------------------------------------------
# Page and status
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
            "can_switch_servers": cfg.can_switch_servers(),
            "auto_accept": cfg.auto_accept,
            "review_floor": cfg.review_floor,
            "indexed": len(state.index) if state.index else None,
            "busy": state.job.stage in RUNNING,
        }
    )


# --------------------------------------------------------------------------
# Connecting, switching, signing out
# --------------------------------------------------------------------------


@app.post("/api/pin")
def api_pin():
    """Start a plex.tv link, returning the code for the user to enter."""
    from plexapi.myplex import MyPlexPinLogin

    try:
        state.pin_login = MyPlexPinLogin()
        state.pin_login.run(timeout=300)
    except Exception as exc:
        return _fail(f"Could not start sign-in: {exc}", 500)
    return jsonify({"pin": state.pin_login.pin})


@app.get("/api/pin/poll")
def api_pin_poll():
    """Has the code been entered yet? Returns the account's servers once it has."""
    from plexapi.myplex import MyPlexAccount

    login = state.pin_login
    if login is None:
        return _fail("No sign-in in progress")
    if not login.checkLogin():
        if login.expired:
            return _fail("The code expired. Start again.", 408)
        return jsonify({"linked": False})

    try:
        account = MyPlexAccount(token=login.token)
        servers = [r for r in account.resources() if "server" in (r.provides or "")]
    except Exception as exc:
        return _fail(f"Signed in, but could not list servers: {exc}", 500)

    cfg = config_mod.load()
    cfg.account_token = login.token
    state.pending_cfg = cfg
    return jsonify(
        {
            "linked": True,
            "username": account.username,
            "servers": [{"name": r.name, "owned": bool(r.owned)} for r in servers],
        }
    )


@app.post("/api/connect/manual")
def api_connect_manual():
    """Connect with a direct address and token instead of signing in."""
    data = request.get_json(force=True)
    cfg = config_mod.load()
    cfg.baseurl = (data.get("baseurl") or "").strip()
    cfg.token = (data.get("token") or "").strip()
    cfg.account_token = ""
    cfg.server_name = ""
    if not cfg.baseurl or not cfg.token:
        return _fail("Both a server address and a token are required.")

    try:
        plex = connect(cfg)
    except PlexError as exc:
        return _fail(str(exc))

    state.pending_cfg = cfg
    return jsonify({"sections": _music_sections(plex), "server": plex.friendlyName})


@app.get("/api/servers")
def api_servers():
    """Every Plex server on the signed-in account, for switching between them."""
    from .plex_index import account_for

    cfg = config_mod.load()
    if not cfg.can_switch_servers():
        return _fail(
            "Switching servers needs a plex.tv sign-in. This install was set up "
            "with a server address and token, which only reaches that one server."
        )
    try:
        account = account_for(cfg)
        servers = [r for r in account.resources() if "server" in (r.provides or "")]
    except Exception as exc:
        return _fail(f"Could not list servers: {exc}", 500)
    return jsonify(
        {
            "current": cfg.server_name,
            "servers": [{"name": r.name, "owned": bool(r.owned)} for r in servers],
        }
    )


@app.post("/api/server")
def api_server():
    """Choose a server, then report its music libraries."""
    data = request.get_json(force=True)
    saved = config_mod.load()
    cfg = _working_config()
    name = (data.get("server_name") or "").strip()
    if not name:
        return _fail("No server chosen.")

    # A different server means a different address, and the cached one belongs
    # to the server being left -- clear it so discovery runs rather than
    # connecting to the machine we are moving away from. The comparison is
    # against the *saved* server: a sign-in flow that was started and abandoned
    # leaves a pending config behind, and trusting its name here would discard
    # a perfectly good cached address on an unrelated later request.
    if name != saved.server_name:
        cfg.baseurl = ""
        cfg.token = ""
    cfg.server_name = name

    try:
        plex = connect(cfg)
    except PlexError as exc:
        return _fail(str(exc))

    cfg.baseurl = plex._baseurl
    cfg.token = plex._token
    state.pending_cfg = cfg
    return jsonify({"sections": _music_sections(plex), "server": plex.friendlyName})


@app.post("/api/finish")
def api_finish():
    """Commit the chosen server and library."""
    data = request.get_json(force=True)
    cfg = _working_config()
    section = (data.get("section") or "").strip()
    if section:
        cfg.section = section
    config_mod.save(cfg)
    state.pending_cfg = None
    state.drop_connection()  # the server or library may have changed
    return jsonify(
        {
            "ok": True,
            "server": cfg.server_name or cfg.baseurl,
            "section": cfg.section,
            "can_switch_servers": cfg.can_switch_servers(),
        }
    )


@app.post("/api/library")
def api_library():
    """Switch music library on the server already connected."""
    data = request.get_json(force=True)
    cfg = config_mod.load()
    section = (data.get("section") or "").strip()
    if not section:
        return _fail("No library chosen.")
    try:
        plex = connect(cfg)
        music_section(plex, section)  # validates it exists and is music
    except PlexError as exc:
        return _fail(str(exc))
    cfg.section = section
    config_mod.save(cfg)
    state.drop_connection()
    return jsonify({"ok": True, "section": section})


@app.get("/api/libraries")
def api_libraries():
    cfg = config_mod.load()
    try:
        plex = connect(cfg)
    except PlexError as exc:
        return _fail(str(exc))
    return jsonify({"current": cfg.section, "sections": _music_sections(plex)})


@app.post("/api/settings")
def api_settings():
    """Update the match thresholds."""
    data = request.get_json(force=True)
    cfg = config_mod.load()
    try:
        auto = float(data.get("auto_accept", cfg.auto_accept))
        floor = float(data.get("review_floor", cfg.review_floor))
    except (TypeError, ValueError):
        return _fail("Thresholds must be numbers.")
    if not 0 <= floor < auto <= 100:
        return _fail("Needs 0 ≤ review floor < auto-accept ≤ 100.")
    cfg.auto_accept, cfg.review_floor = auto, floor
    config_mod.save(cfg)
    return jsonify({"ok": True, "auto_accept": auto, "review_floor": floor})


@app.post("/api/signout")
def api_signout():
    """Forget the server, its token and its cached library."""
    data = request.get_json(silent=True) or {}
    config_mod.sign_out(forget_decisions=bool(data.get("forget_decisions")))
    state.drop_connection()
    state.pending_cfg = None
    state.pin_login = None
    return jsonify({"ok": True})


@app.post("/api/index/refresh")
def api_index_refresh():
    """Rebuild the cached library index from the server."""
    if state.job.stage in RUNNING:
        return _fail("Busy — wait for the current sync to finish.", 409)
    cfg = config_mod.load()
    try:
        state.plex = connect(cfg)
        state.index = load_index(state.plex, cfg, refresh=True)
    except PlexError as exc:
        return _fail(str(exc))
    return jsonify({"ok": True, "indexed": len(state.index)})


# --------------------------------------------------------------------------
# Syncing
# --------------------------------------------------------------------------


def _run_sync(
    url: str, name: str, refresh_playlist: bool, preview: bool, reorder: bool
) -> None:
    job = state.job
    job.preview = preview
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
                f"could be read from {playlist.source_label}. {playlist.truncated_note}"
            )

        job.stage = "index"
        job.message = "Reading your Plex library — the first run can take a few minutes…"
        if state.plex is None:
            state.plex = connect(cfg)
        if state.index is None:
            state.index = load_index(state.plex, cfg)
        job.message = f"{len(state.index)} tracks in your library"

        job.stage, job.message = "match", f"Matching {len(playlist.tracks)} tracks…"
        matcher = Matcher(
            state.index, auto_accept=cfg.auto_accept, review_floor=cfg.review_floor
        )
        results = matcher.match_all(playlist.tracks)

        state.decisions = load_decisions()
        apply_decisions(results, state.decisions, state.index)
        job.results = results

        if preview:
            job.stage = "done"
            job.message = "Preview only — nothing was written to Plex."
            return

        job.stage, job.message = "writing", "Writing the playlist to Plex…"
        keys = matched_keys(results)
        outcome = sync_playlist(
            state.plex,
            job.playlist_title,
            keys,
            summary=build_summary(playlist.url, len(keys), len(results)),
            reorder=reorder,
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
    if state.job.stage in RUNNING:
        return _fail("A sync is already running.", 409)

    data = request.get_json(force=True)
    url = (data.get("url") or "").strip()
    if not url:
        return _fail("Paste a YouTube Music or Spotify playlist link first.")

    preview = bool(data.get("preview"))
    state.job = Job(stage="playlist", message="Starting…", preview=preview)
    threading.Thread(
        target=_run_sync,
        args=(
            url,
            (data.get("name") or "").strip(),
            bool(data.get("refresh_playlist")),
            preview,
            not data.get("no_reorder"),
        ),
        daemon=True,
    ).start()
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
        "preview": job.preview,
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
        return _fail("Unknown track")

    result = results[idx]
    if action == "add":
        rating_key = data.get("rating_key")
        candidate = next(
            (c for c in result.candidates if c.track.rating_key == rating_key),
            result.candidates[0] if result.candidates else None,
        )
        if candidate is None:
            return _fail("Nothing to add")
        result.status = MANUAL
        result.chosen = candidate.track
        result.note = "added in review"
        remember(
            state.decisions,
            result.yt.track_id,
            candidate.track.rating_key,
            candidate.track.display,
            index=state.index,
        )
    elif action == "ignore":
        result.status = SKIPPED
        result.chosen = None
        result.note = "ignored in review"
        remember(state.decisions, result.yt.track_id, None, "ignored")
    elif action == "undo":
        # Put the track back in the queue and forget the saved decision.
        forget(state.decisions, result.yt.track_id)
        result.status = REVIEW if result.candidates else MISSING
        result.chosen = None
        result.note = ""
    else:
        return _fail("Unknown action")

    save_decisions(state.decisions)
    state.job.pending += 1
    return jsonify(
        {
            "ok": True,
            "pending": state.job.pending,
            "counts": _counts(results),
            "result": _result_json(idx, result),
        }
    )


@app.post("/api/apply")
def api_apply():
    """Write the accumulated review decisions into the Plex playlist, in order."""
    job = state.job
    if not job.results or state.plex is None:
        return _fail("Nothing to apply")

    data = request.get_json(silent=True) or {}
    keys = matched_keys(job.results)
    try:
        outcome = sync_playlist(
            state.plex,
            job.playlist_title,
            keys,
            summary=build_summary(job.playlist_url, len(keys), len(job.results)),
            reorder=not data.get("no_reorder"),
        )
    except Exception as exc:
        return _fail(f"Could not update the playlist: {exc}", 500)

    job.pending = 0
    job.written = outcome.action
    job.preview = False
    return jsonify({"ok": True, "total": outcome.total, "action": outcome.action})


# --------------------------------------------------------------------------
# Reports
# --------------------------------------------------------------------------


@app.get("/api/report/<kind>")
def api_report(kind: str):
    job = state.job
    if not job.results:
        return _fail("Nothing to report yet")

    slug = slugify(job.playlist_title)
    if kind == "csv":
        body, mime, name = csv_bytes(job.results), "text/csv", f"report-{slug}.csv"
    elif kind == "wanted":
        body, mime, name = wanted_bytes(job.results), "text/plain", f"wanted-{slug}.txt"
    else:
        return _fail("Unknown report")

    return Response(
        body,
        mimetype=mime,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
