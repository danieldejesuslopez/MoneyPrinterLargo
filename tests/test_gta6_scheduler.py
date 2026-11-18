"""Real SQLite dispatch reservations and API integration; no network or rendering."""

import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "webapp" / "api"))

from gta6 import Workspace, autopilot, clipping
from gta6 import editorial as ed
from gta6.scheduler import AutopilotScheduler
import gta6_api


@pytest.fixture
def workspace(tmp_path):
    return Workspace(tmp_path / "editorial")


def test_scheduler_is_disabled_until_configured_and_preserves_next_due(workspace):
    calls = []
    spawn = lambda args, **kw: calls.append(args) or SimpleNamespace(id="job", status="done")
    scheduler = AutopilotScheduler(workspace, spawn)
    assert scheduler.tick() is None
    autopilot.configure(workspace, {"enabled": True})
    assert scheduler.tick().id == "job"
    assert calls == [["gta6", "autopilot"]]
    # A new API process sees the durable reservation/next due time.
    assert AutopilotScheduler(workspace, spawn).tick() is None
    assert len(calls) == 1


def test_manual_scan_while_paused_and_running_process_guard(workspace):
    calls = []
    scheduler = AutopilotScheduler(workspace, lambda args, **kw: calls.append(args) or SimpleNamespace(id="job", status="running"))
    scheduler.dispatch(produce=False)
    assert calls == [["gta6", "autopilot", "--scan-only"]]
    with pytest.raises(ValueError, match="marcha"):
        scheduler.dispatch(produce=False)


def test_failed_dispatch_clears_reservation_and_schedules_backoff(workspace):
    def fail(*args, **kwargs):
        raise OSError("spawn failed")
    autopilot.configure(workspace, {"enabled": True})
    with pytest.raises(OSError):
        AutopilotScheduler(workspace, fail).dispatch()
    auto = workspace.store.read()["autopilot"]
    assert auto["dispatch_until"] == ""
    assert "spawn failed" in auto["last_error"]
    assert timedelta(minutes=4) < ed.parse_time(auto["next_scan_at"]) - ed.now() < timedelta(minutes=6)


def test_api_config_validation_and_shared_background_jobs(workspace, monkeypatch):
    monkeypatch.setattr(gta6_api, "workspace", workspace)
    monkeypatch.setattr(gta6_api, "_autopilot_scheduler", None)
    calls = []
    monkeypatch.setattr(gta6_api, "_spawn_job", lambda args, **kw: calls.append(args) or SimpleNamespace(id="auto123", status="done"))
    app = FastAPI()
    app.include_router(gta6_api.router)
    with TestClient(app) as client:
        assert client.get("/api/gta6/autopilot").json()["config"]["enabled"] is False
        assert client.patch("/api/gta6/autopilot/config", json={"interval_minutes": 0}).status_code == 422
        assert client.patch("/api/gta6/autopilot/config", json={"auto_publish": True}).status_code == 422
        response = client.post("/api/gta6/autopilot/run", json={"produce": False})
        assert response.status_code == 200
        assert response.json()["stream_url"] == "/api/jobs/auto123/stream"
        assert calls == [["gta6", "autopilot", "--scan-only"]]
        source = client.post("/api/gta6/sources", json={"name": "Creator", "kind": "creator", "url": "https://twitch.tv/creator", "monitor_url": "https://youtube.com/@creator", "monitor_enabled": False})
        assert source.status_code == 200
        assert source.json()["monitor_enabled"] is False
        updated = client.put(f"/api/gta6/sources/{source.json()['id']}", json={"name": "Renamed", "kind": "creator", "url": "https://twitch.tv/creator"})
        assert updated.json()["monitor_enabled"] is False
        assert updated.json()["monitor_url"] == "https://youtube.com/@creator"
        assert client.post("/api/gta6/sources", json={"name": "Bad", "kind": "creator", "url": "https://youtube.com/@bad", "monitor_url": "http://127.0.0.1/private"}).status_code == 400


def test_autopilot_request_is_atomically_bound_and_pause_checked_before_render(workspace):
    source = workspace.source({"name": "Own", "url": "https://youtube.com/@own", "kind": "creator", "permission": "own", "permission_note": "Own recordings", "credit": "@own"})
    with workspace.store.edit() as state:
        state["autopilot"]["config"]["enabled"] = True
        state["autopilot"]["candidates"].append({"id": "candidate", "status": "queued", "request_id": ""})
    data = {"source_id": source["id"], "target": "https://youtube.com/watch?v=abcdefghijk", "commentary": "¿Qué harías en este momento?", "autopilot_candidate_id": "candidate"}
    first = clipping.request(workspace, data)
    assert clipping.request(workspace, data)["id"] == first["id"]
    saved = workspace.store.read()
    assert saved["autopilot"]["candidates"][0]["request_id"] == first["id"]
    assert len(saved["clip_jobs"]) == 1
    autopilot.configure(workspace, {"enabled": False})
    with pytest.raises(ValueError, match="pausado"):
        clipping.run(workspace, first["id"])
    assert workspace.store.read()["clip_jobs"][0]["status"] == "queued"


def test_source_edit_during_render_cannot_reattribute_the_draft(workspace):
    source = workspace.source({"name": "Own", "url": "https://youtube.com/@own", "kind": "creator", "permission": "own", "permission_note": "Own recordings", "credit": "@own"})
    identity = autopilot._source_identity(source)
    workspace.source({"url": "https://youtube.com/@different"}, source["id"])
    with pytest.raises(ValueError, match="cambiaron"):
        workspace.post({"title": "GTA moment", "text": "Un momento", "source_id": source["id"]}, expected_source_identity=identity)
    assert not workspace.store.read()["posts"]
