"""Web API. The sync route mutates the user's server, so everything here runs
against stubs -- no Plex, no network."""

import pytest

from playlistamped import webapp
from playlistamped.matcher import AUTO, MANUAL, MISSING, REVIEW, Candidate, MatchResult
from playlistamped.webapp import Job, app, state
from tests.test_report import plex_track, yt
from tests.test_sync import FakePlex


@pytest.fixture
def client(monkeypatch, tmp_path):
    # Keep decisions out of the real cache dir.
    monkeypatch.setattr(webapp, "save_decisions", lambda d: None)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def candidate(key, title, artist, score):
    return Candidate(
        track=plex_track(key, title, artist),
        score=score,
        components={"title": 90.0, "artist": 88.0, "album": 50.0, "duration": 95.0, "penalty": 0.0},
    )


@pytest.fixture
def loaded_job():
    hit = MatchResult(yt=yt("Matched Song", ["Band"], 200, "a"), status=AUTO)
    hit.chosen = plex_track("11", "Matched Song", "Band")

    needs = MatchResult(
        yt=yt("Borderline Song", ["Band"], 200, "b"),
        status=REVIEW,
        candidates=[candidate("22", "Borderline Song", "Band", 78), candidate("33", "Other", "Band", 70)],
    )
    gone = MatchResult(yt=yt("Absent Song", ["Nobody"], 200, "c"), status=MISSING)

    state.job = Job(stage="done", playlist_title="Test Mix", playlist_url="http://yt", total=3)
    state.job.results = [hit, needs, gone]
    state.decisions = {}
    state.plex = FakePlex()
    yield state.job
    state.job = Job()


def test_state_reports_configuration(client, monkeypatch):
    from playlistamped.config import Config

    monkeypatch.setattr(webapp.config_mod, "load", lambda: Config(baseurl="u", token="t", section="Music"))
    body = client.get("/api/state").get_json()
    assert body["configured"] is True and body["section"] == "Music"


def test_page_renders(client):
    page = client.get("/")
    assert page.status_code == 200
    assert b"playlist" in page.data


def test_sync_requires_a_url(client):
    assert client.post("/api/sync", json={"url": "  "}).status_code == 400


def test_job_splits_results_into_buckets(client, loaded_job):
    body = client.get("/api/job").get_json()
    assert body["counts"] == {"matched": 1, "review": 1, "missing": 1, "skipped": 0}
    assert len(body["review"]) == 1
    assert body["review"][0]["candidates"][0]["rating_key"] == "22"
    assert body["matched"][0]["chosen"]["title"] == "Matched Song"


def test_add_records_the_chosen_candidate(client, loaded_job):
    body = client.post("/api/decide", json={"index": 1, "action": "add", "rating_key": "22"}).get_json()
    assert body["counts"]["matched"] == 2 and body["counts"]["review"] == 0
    assert loaded_job.results[1].status == MANUAL
    assert loaded_job.results[1].chosen.rating_key == "22"


def test_add_can_pick_a_runner_up(client, loaded_job):
    """The 'other possibilities' list must be able to win over the top match."""
    client.post("/api/decide", json={"index": 1, "action": "add", "rating_key": "33"})
    assert loaded_job.results[1].chosen.rating_key == "33"


def test_ignore_drops_the_track_and_is_remembered(client, loaded_job):
    body = client.post("/api/decide", json={"index": 1, "action": "ignore"}).get_json()
    assert body["counts"]["review"] == 0 and body["counts"]["skipped"] == 1
    assert loaded_job.results[1].chosen is None
    assert state.decisions["b"]["rating_key"] is None


def test_decisions_persist_so_a_track_is_judged_once(client, loaded_job):
    client.post("/api/decide", json={"index": 1, "action": "add", "rating_key": "22"})
    assert state.decisions["b"]["rating_key"] == "22"


def test_decide_rejects_a_bad_index(client, loaded_job):
    assert client.post("/api/decide", json={"index": 99, "action": "add"}).status_code == 400


def test_decide_rejects_an_unknown_action(client, loaded_job):
    assert client.post("/api/decide", json={"index": 1, "action": "delete"}).status_code == 400


def test_apply_writes_every_matched_track_in_order(client, loaded_job):
    client.post("/api/decide", json={"index": 1, "action": "add", "rating_key": "22"})
    body = client.post("/api/apply", json={}).get_json()
    assert body["total"] == 2
    created = state.plex.playlists()[0]
    assert [str(i.ratingKey) for i in created.items()] == ["11", "22"]


def test_apply_without_results_is_rejected(client):
    state.job = Job()
    assert client.post("/api/apply", json={}).status_code == 400


# ---------------------------------------------------------------- settings,
# server switching and sign-out: the features that used to require the CLI.


@pytest.fixture
def fake_config(monkeypatch, tmp_path):
    """A config that lives in memory, so tests never touch the real one."""
    from playlistamped.config import Config

    held = {"cfg": Config(account_token="acct", server_name="Home", section="Music")}
    monkeypatch.setattr(webapp.config_mod, "load", lambda: held["cfg"])
    monkeypatch.setattr(webapp.config_mod, "save", lambda c: held.update(cfg=c))
    return held


class FakeSection:
    def __init__(self, title, kind="artist"):
        self.title, self.type = title, kind


class FakeLibrary:
    def sections(self):
        return [FakeSection("Music"), FakeSection("Ambient"), FakeSection("Films", "movie")]


class FakeServer:
    friendlyName = "Home"
    _baseurl = "http://found:32400"
    _token = "tok"
    library = FakeLibrary()


def test_state_exposes_what_the_settings_panel_needs(client, fake_config):
    body = client.get("/api/state").get_json()
    assert body["can_switch_servers"] is True
    assert body["auto_accept"] == 88.0 and body["review_floor"] == 65.0


def test_libraries_lists_only_music_sections(client, fake_config, monkeypatch):
    monkeypatch.setattr(webapp, "connect", lambda cfg: FakeServer())
    body = client.get("/api/libraries").get_json()
    assert body["sections"] == ["Music", "Ambient"]  # the movie section is excluded


def test_switching_library_is_saved(client, fake_config, monkeypatch):
    monkeypatch.setattr(webapp, "connect", lambda cfg: FakeServer())
    monkeypatch.setattr(webapp, "music_section", lambda plex, name: FakeSection(name))
    assert client.post("/api/library", json={"section": "Ambient"}).status_code == 200
    assert fake_config["cfg"].section == "Ambient"


def test_switching_library_drops_the_cached_index(client, fake_config, monkeypatch):
    """The index belongs to the old library and would match against it."""
    monkeypatch.setattr(webapp, "connect", lambda cfg: FakeServer())
    monkeypatch.setattr(webapp, "music_section", lambda plex, name: FakeSection(name))
    state.index = object()
    client.post("/api/library", json={"section": "Ambient"})
    assert state.index is None


def test_servers_needs_a_plex_tv_sign_in(client, fake_config):
    from playlistamped.config import Config

    fake_config["cfg"] = Config(baseurl="u", token="t")  # direct token only
    body = client.get("/api/servers")
    assert body.status_code == 400
    assert "plex.tv" in body.get_json()["error"]


def test_switching_server_clears_the_old_address(client, fake_config, monkeypatch):
    """The cached address points at the server being left."""
    fake_config["cfg"].baseurl = "http://old:32400"
    fake_config["cfg"].token = "old-token"
    seen = {}

    def watch(cfg):
        seen["baseurl"] = cfg.baseurl
        return FakeServer()

    monkeypatch.setattr(webapp, "connect", watch)
    client.post("/api/server", json={"server_name": "Other"})
    assert seen["baseurl"] == ""  # forced rediscovery


def test_switching_server_keeps_the_address_when_it_is_the_same_one(client, fake_config, monkeypatch):
    fake_config["cfg"].baseurl = "http://same:32400"
    seen = {}
    monkeypatch.setattr(webapp, "connect", lambda cfg: (seen.update(u=cfg.baseurl), FakeServer())[1])
    client.post("/api/server", json={"server_name": "Home"})
    assert seen["u"] == "http://same:32400"


def test_server_requires_a_name(client, fake_config):
    assert client.post("/api/server", json={"server_name": " "}).status_code == 400


def test_thresholds_are_validated(client, fake_config):
    assert client.post("/api/settings", json={"auto_accept": 50, "review_floor": 80}).status_code == 400
    assert client.post("/api/settings", json={"auto_accept": 200, "review_floor": 5}).status_code == 400
    assert client.post("/api/settings", json={"auto_accept": "x"}).status_code == 400


def test_thresholds_are_saved(client, fake_config):
    body = client.post("/api/settings", json={"auto_accept": 92, "review_floor": 70}).get_json()
    assert body["auto_accept"] == 92
    assert fake_config["cfg"].review_floor == 70


def test_sign_out_clears_state(client, fake_config, monkeypatch):
    called = {}
    monkeypatch.setattr(webapp.config_mod, "sign_out", lambda **kw: called.update(kw))
    state.plex = object()
    assert client.post("/api/signout", json={}).status_code == 200
    assert state.plex is None
    assert called == {"forget_decisions": False}


def test_index_refresh_is_refused_while_a_sync_runs(client, fake_config):
    state.job = Job(stage="match")
    assert client.post("/api/index/refresh", json={}).status_code == 409
    state.job = Job()


def test_undo_returns_a_track_to_the_queue(client, loaded_job):
    client.post("/api/decide", json={"index": 1, "action": "ignore"})
    body = client.post("/api/decide", json={"index": 1, "action": "undo"}).get_json()
    assert body["counts"]["review"] == 1
    assert "b" not in state.decisions


def test_reports_download(client, loaded_job):
    csv_res = client.get("/api/report/csv")
    assert csv_res.status_code == 200
    assert "attachment" in csv_res.headers["Content-Disposition"]
    assert b"Matched Song" in csv_res.data

    wanted = client.get("/api/report/wanted")
    assert b"Absent Song" in wanted.data


def test_unknown_report_is_rejected(client, loaded_job):
    assert client.get("/api/report/nope").status_code == 400


def test_reports_before_a_run_are_rejected(client):
    state.job = Job()
    assert client.get("/api/report/csv").status_code == 400


def test_preview_does_not_write_to_plex(client, fake_config, monkeypatch):
    """Preview must match and report without touching the server."""
    from playlistamped.plex_index import PlexIndex

    plex = FakePlex()
    monkeypatch.setattr(webapp, "connect", lambda cfg: plex)
    monkeypatch.setattr(webapp, "load_index", lambda *a, **k: PlexIndex("Music", "m", 0, []))
    monkeypatch.setattr(webapp, "fetch_playlist", lambda *a, **k: _tiny_playlist())
    monkeypatch.setattr(webapp, "load_decisions", dict)

    webapp._run_sync("http://x", "", False, True, True)
    assert state.job.stage == "done"
    assert state.job.preview is True
    assert plex.created == []


def _tiny_playlist():
    from playlistamped.sources import Playlist

    pl = Playlist(id="p", title="Tiny", author="", url="http://x")
    pl.tracks = [yt("Song", ["Band"], 200, "t1")]
    return pl


def test_an_abandoned_sign_in_cannot_disturb_a_working_config(client, fake_config, monkeypatch):
    """A setup flow started and walked away from leaves a pending config; it
    must not be picked up by a later, unrelated request."""
    from playlistamped.config import Config

    state.pending_cfg = Config(account_token="stale", server_name="Ghost")
    fake_config["cfg"].baseurl = "http://live:32400"
    fake_config["cfg"].token = "live"
    seen = {}
    monkeypatch.setattr(
        webapp, "connect", lambda cfg: (seen.update(u=cfg.baseurl), FakeServer())[1]
    )
    client.post("/api/server", json={"server_name": "Home"})
    state.pending_cfg = None
    assert seen["u"] == "http://live:32400"
