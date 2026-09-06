"""Web API. The sync route mutates the user's server, so everything here runs
against stubs -- no Plex, no network."""

import pytest

from playlistamp import webapp
from playlistamp.matcher import AUTO, MANUAL, MISSING, REVIEW, Candidate, MatchResult
from playlistamp.webapp import Job, app, state
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
    from playlistamp.config import Config

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
