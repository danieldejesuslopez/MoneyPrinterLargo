"""Editorial state transitions and recovery, without real social-network writes."""

import io
import json
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "webapp" / "api"))

from gta6 import Workspace
from gta6 import clipping, editorial as ed
from gta6.buffer import BufferClient, BufferError, dispatch, public_media_url, resolve
from gta6.service import find
import gta6_api


@pytest.fixture
def workspace(tmp_path):
    return Workspace(tmp_path / "editorial")


@pytest.fixture
def client(workspace, monkeypatch):
    monkeypatch.setattr(gta6_api, "workspace", workspace)
    app = FastAPI()
    app.include_router(gta6_api.router)
    return TestClient(app)


def draft(workspace, **changes):
    return workspace.post({"title": "Primera ruta", "text": "¿Qué explorarás primero?", "reviewed": True, **changes})


def approved(workspace, **changes):
    row = draft(workspace, **changes)
    return workspace.approve(row["id"])


def planned(workspace):
    row = approved(workspace)
    return workspace.plan(row["id"], (ed.now() + timedelta(hours=3)).isoformat())


def fact(workspace):
    return workspace.fact({"text": "Un dato comprobado", "url": "https://www.rockstargames.com/VI", "status": "verified", "reviewed": True})


def test_starts_without_invented_facts_metrics_or_posts(workspace):
    state = workspace.snapshot()
    assert state["settings"]["timezone"] == "America/La_Paz"
    assert len(state["sources"]) == 10
    assert not any(state[k] for k in ("facts", "metrics", "posts"))
    assert state["review"]["sample"] == 0
    assert state["countdown"]["days"] is None


def test_state_survives_a_new_workspace_instance(workspace):
    row = draft(workspace)
    assert Workspace(workspace.store.directory).snapshot()["posts"][0]["id"] == row["id"]


def test_concurrent_writers_keep_every_idea(workspace):
    with ThreadPoolExecutor(max_workers=5) as executor:
        list(executor.map(lambda n: workspace.idea({"title": f"Pregunta {n}"}), range(25)))
    assert len(workspace.snapshot()["ideas"]) == 25


def test_failed_transaction_does_not_change_data(workspace):
    with pytest.raises(RuntimeError):
        with workspace.store.edit() as state:
            state["settings"]["name"] = "Lost"
            raise RuntimeError()
    assert workspace.snapshot()["settings"]["name"] == "GTA 6"


@pytest.mark.parametrize("url", ["https://example.com/official", "https://rockstargames.com.evil.test/VI", "https://x.com/RockstarGames"])
def test_only_official_web_pages_can_back_verified_facts(workspace, url):
    with pytest.raises(ValueError, match="oficial"):
        workspace.fact({"text": "Not verified", "url": url, "status": "verified", "reviewed": True})


def test_verification_requires_a_human_review(workspace):
    with pytest.raises(ValueError):
        workspace.fact({"text": "Dato", "url": "https://www.rockstargames.com/VI", "status": "verified"})


def test_expired_fact_prevents_approval(workspace):
    f = fact(workspace)
    row = draft(workspace, pillar="news", fact_ids=[f["id"]])
    with workspace.store.edit() as state:
        state["facts"][0]["verified_at"] = (ed.now() - timedelta(hours=49)).isoformat()
    with pytest.raises(ValueError, match="48 h"):
        workspace.approve(row["id"])


def test_rechecking_a_fact_invalidates_prior_approval(workspace):
    f = fact(workspace)
    row = approved(workspace, pillar="news", fact_ids=[f["id"]])
    workspace.fact({**{k: f[k] for k in ("text", "url")}, "status": "verified", "reviewed": True}, f["id"])
    post = workspace.snapshot()["posts"][0]
    assert post["status"] == "draft"
    assert not post["reviewed"]


def test_editing_after_approval_clears_the_schedule(workspace):
    row = planned(workspace)
    updated = workspace.post({"text": "Una pregunta nueva"}, row["id"])
    assert updated["status"] == "draft"
    assert not updated["scheduled_at"]
    assert not updated["approved_at"]


def test_cannot_publish_an_unapproved_draft(workspace):
    row = draft(workspace)
    with pytest.raises(ValueError):
        workspace.published(row["id"], "https://x.com/fan/status/1")


def test_duplicate_same_network_and_text_is_rejected(workspace):
    approved(workspace)
    row = draft(workspace, title="Otro")
    with pytest.raises(ValueError, match="siete días"):
        workspace.approve(row["id"])


def test_caption_rules_preserve_credit(workspace):
    assert ed.copy_for("Detalle #GTA6", "x", "@author") == "Detalle\n\nCrédito: @author"
    assert ed.copy_for("Detalle", "instagram", "@author", True).startswith("SPOILER")
    assert "#GTA6 #GTAVI #ViceCity" in ed.copy_for("Detalle", "instagram")


def test_local_schedule_is_interpreted_in_workspace_zone(workspace):
    row = approved(workspace)
    tomorrow = (ed.now().astimezone(ed.get_zone("America/La_Paz")) + timedelta(days=1)).date()
    scheduled = workspace.plan(row["id"], f"{tomorrow.isoformat()}T10:00")
    assert scheduled["scheduled_at"].endswith("14:00:00+00:00")


def test_nonexistent_dst_wall_time_is_refused():
    with pytest.raises(ValueError, match="no existe"):
        ed.local_time("2026-03-08T02:30", "America/New_York")


def test_no_slot_collisions(workspace):
    first = approved(workspace)
    second = approved(workspace, text="¿Qué vehículo elegirías?")
    due = (ed.now() + timedelta(days=1)).isoformat()
    workspace.plan(first["id"], due)
    with pytest.raises(ValueError, match="ocupado"):
        workspace.plan(second["id"], due)


def test_daily_plan_only_reserves_approved_posts(workspace):
    approved(workspace)
    draft(workspace, text="¿Qué personaje recuerdas?")
    rows = workspace.day_plan((ed.now() + timedelta(days=1)).date().isoformat())
    assert len(rows) == 1
    assert rows[0]["status"] == "planned"
    assert sum(p["status"] == "draft" for p in workspace.snapshot()["posts"]) == 1


def test_metrics_keep_unavailable_values_blank_and_zero_real(workspace):
    row = approved(workspace)
    workspace.published(row["id"], "https://x.com/fan/status/1", (ed.now() - timedelta(hours=25)).isoformat())
    metric = workspace.metric(row["id"], 24, {"views": 0})
    assert metric["views"] == 0
    assert metric["saves"] is None
    assert workspace.snapshot()["review"]["top"][0]["views"] == 0


def test_no_metrics_before_the_measurement_age(workspace):
    row = approved(workspace)
    workspace.published(row["id"], "https://x.com/fan/status/1")
    with pytest.raises(ValueError, match="Todavía"):
        workspace.metric(row["id"], 24, {"views": 10})


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), 1.4, True])
def test_invalid_observations_are_rejected(value):
    with pytest.raises(ValueError):
        ed.validate_metric({"views": value})


def test_weekly_review_does_not_mix_read_ages(workspace):
    first = approved(workspace)
    second = approved(workspace, text="¿Tu misión favorita?")
    for p in (first, second):
        workspace.published(p["id"], "https://x.com/fan/status/1", (ed.now() - timedelta(hours=74)).isoformat())
    workspace.metric(first["id"], 24, {"views": 500})
    workspace.metric(second["id"], 72, {"views": 20})
    review = workspace.snapshot()["review"]
    assert review["sample"] == 1
    assert review["read_hours"] == 72
    assert review["top"][0]["id"] == second["id"]


def test_one_variable_test_per_day_and_no_early_verdict(workspace):
    row = workspace.note("experiments", {"name": "Gancho", "variable": "hook", "control": "Pregunta", "variant": "Detalle"})
    with pytest.raises(ValueError, match="hoy"):
        workspace.note("experiments", {"name": "Horario", "variable": "time", "control": "10", "variant": "12"})
    with pytest.raises(ValueError, match="24 horas"):
        workspace.finish_experiment(row["id"], "Ganó B")


def test_takedown_keeps_remote_ids_and_blocks_all_source_posts(workspace):
    source = workspace.source({"name": "Author", "url": "https://youtube.com/@author", "kind": "creator", "credit": "@author", "permission": "granted", "permission_note": "Written permission"})
    row = draft(workspace, source_id=source["id"])
    with workspace.store.edit() as state:
        state["posts"][0].update(status="scheduled", buffer_post_id="remote123")
    workspace.takedown(source["id"], "Creator requested removal")
    post = workspace.snapshot()["posts"][0]
    assert post["status"] == "blocked"
    assert post["buffer_post_id"] == "remote123"


class FakeBuffer:
    def __init__(self, failure=False):
        self.calls = 0
        self.failure = failure

    def channels(self):
        return [{"id": "channel", "service": "twitter", "isQueuePaused": False}]

    def create(self, post, channel_id):
        self.calls += 1
        if self.failure:
            raise BufferError("timeout")
        return {"id": "remote-post", "dueAt": post["scheduled_at"]}


def test_buffer_scheduling_is_explicit_and_cannot_be_repeated(workspace):
    workspace.settings({"buffer_channels": {"x": "channel"}})
    row = planned(workspace)
    fake = FakeBuffer()
    assert fake.calls == 0
    assert dispatch(workspace, row["id"], fake)["status"] == "scheduled"
    with pytest.raises(ValueError):
        dispatch(workspace, row["id"], fake)
    assert fake.calls == 1


def test_uncertain_buffer_response_requires_reconciliation(workspace):
    workspace.settings({"buffer_channels": {"x": "channel"}})
    row = planned(workspace)
    fake = FakeBuffer(failure=True)
    with pytest.raises(BufferError):
        dispatch(workspace, row["id"], fake)
    assert workspace.snapshot()["posts"][0]["status"] == "pending_confirmation"
    with pytest.raises(ValueError):
        dispatch(workspace, row["id"], fake)
    assert fake.calls == 1
    assert resolve(workspace, row["id"], "not_created")["status"] == "approved"


@pytest.mark.parametrize("url", ["http://example.com/a.mp4", "https://localhost/a.mp4", "https://127.0.0.1/a.mp4", "https://example.com/share/123", "https://example.com/a.mp4?expires=123"])
def test_buffer_refuses_nonpublic_or_temporary_media(url):
    with pytest.raises(ValueError):
        public_media_url(url)


def test_current_buffer_api_payload(monkeypatch):
    client = BufferClient("secret-never-exported")
    captured = []
    monkeypatch.setattr(client, "query", lambda query, variables=None: captured.append(variables) or {"createPost": {"post": {"id": "ok"}}})
    client.create({"text": "clip", "title": "Hook", "format": "video", "platform": "instagram", "scheduled_at": "2027-01-01T10:00:00Z", "media_url": "https://media.example.com/clip.mp4"}, "channel")
    value = captured[0]["input"]
    assert value["mode"] == "customScheduled"
    assert value["assets"] == [{"video": {"url": "https://media.example.com/clip.mp4"}}]
    assert value["metadata"]["instagram"]["type"] == "reel"


def test_export_contains_portable_files_and_blanks(workspace, monkeypatch):
    monkeypatch.setenv("BUFFER_API_KEY", "never-export-this")
    draft(workspace)
    with zipfile.ZipFile(io.BytesIO(workspace.export())) as archive:
        assert {"GTA 6/playbook.md", "GTA 6/scoreboard.csv", "GTA 6/calendar.csv", "GTA 6/media/avatar.svg", "GTA 6/media/banner.svg"} <= set(archive.namelist())
        assert all(b"never-export-this" not in archive.read(name) for name in archive.namelist())
        assert "None" not in archive.read("GTA 6/scoreboard.csv").decode()


def test_no_llm_fallback_does_not_invent_15_facts(workspace):
    result = workspace.generate_ideas(15, False)
    assert len(result["ideas"]) == 5
    assert result["warning"]
    assert not workspace.generate_ideas(15, False)["ideas"]


def test_api_validates_malformed_payloads_and_errors(client):
    assert client.get("/api/gta6").status_code == 200
    assert client.post("/api/gta6/posts", json={}).status_code == 400
    assert client.post("/api/gta6/posts", json={"title": "A", "text": "B", "fact_ids": "oops"}).status_code == 422
    assert client.post("/api/gta6/posts/missing/approve", json={}).status_code == 404
    assert client.patch("/api/gta6/settings", json={"timezone": "Mars/Cydonia"}).status_code == 400
    assert client.post("/api/gta6/notes/not-a-collection", json={}).status_code == 400


def test_clip_request_reuses_job_registry(client, workspace, monkeypatch, tmp_path):
    source = workspace.source({"name": "Mine", "url": "https://youtube.com/@mine", "kind": "creator", "credit": "@mine", "permission": "own", "permission_note": "Own recording"})
    target = tmp_path / "input.mp4"
    target.write_bytes(b"video")
    calls = []
    class Job:
        id = "job123"
    monkeypatch.setattr(gta6_api, "_spawn_job", lambda args, **kw: calls.append(args) or Job())
    response = client.post("/api/gta6/clips", json={"target": str(target), "source_id": source["id"], "commentary": "Observe this moment"})
    assert response.status_code == 200
    assert response.json()["stream_url"] == "/api/jobs/job123/stream"
    assert calls[0][:3] == ["gta6", "clips", "--request-id"]


def test_gta_profile_passes_bounds_without_changing_global_settings(workspace, monkeypatch, tmp_path):
    from clips import pipeline, store as clip_store
    target = tmp_path / "input.mp4"
    target.write_bytes(b"video")
    source = workspace.source({"name": "Mine", "url": "https://youtube.com/@mine", "kind": "creator", "credit": "@mine", "permission": "own", "permission_note": "Own recording"})
    job = clipping.request(workspace, {"target": str(target), "source_id": source["id"], "commentary": "Look at the turn"})
    captured = []
    def fake_run(target, **kwargs):
        captured.append(kwargs)
        return {"clips": [{"id": "clip123", "title": "That turn", "duration": 12}], "failures": []}
    monkeypatch.setattr(pipeline, "run", fake_run)
    monkeypatch.setattr(clip_store, "update", lambda *a: None)
    result = clipping.run(workspace, job["id"], on_status=lambda line: None)
    assert captured[0]["duration_bounds"] == (7.0, 30.0)
    assert captured[0]["title_overlay"] == "always"
    assert "GTA 6" in captured[0]["editorial_brief"]
    assert result["posts"][0]["status"] == "draft"
    assert "@mine" in result["posts"][0]["text"]


def test_profile_uses_short_windows_without_consulting_global_duration(monkeypatch):
    from clips import pipeline, trends
    words = []
    for sentence in range(12):
        for index in range(6):
            start = sentence * 3 + index * 0.45
            words.append({"word": "detalle." if index == 5 else "Mira", "start": start, "end": start + 0.4})
    monkeypatch.setattr(trends, "duration_window", lambda *a: pytest.fail("global duration should not be consulted"))
    chosen, candidates, report = pipeline.plan_clips(words, 2, use_llm=False, duration_bounds=(7, 30), editorial_brief=clipping.BRIEF)
    assert chosen
    assert all(7 <= window.duration <= 30 for window in chosen)


def test_fact_must_remain_valid_at_the_scheduled_time(workspace):
    f = fact(workspace)
    post = approved(workspace, pillar="news", fact_ids=[f["id"]])
    with pytest.raises(ValueError, match="vencerán"):
        workspace.plan(post["id"], (ed.now() + timedelta(hours=50)).isoformat())


def test_buffer_success_does_not_erase_a_concurrent_takedown(workspace):
    source = workspace.source({"name": "Author", "url": "https://youtube.com/@author", "kind": "creator", "credit": "@author", "permission": "granted", "permission_note": "Permission"})
    post = approved(workspace, source_id=source["id"])
    workspace.plan(post["id"], (ed.now() + timedelta(hours=2)).isoformat())
    workspace.settings({"buffer_channels": {"x": "channel"}})
    class ConcurrentBuffer(FakeBuffer):
        def create(self, post, channel_id):
            workspace.takedown(source["id"], "Withdrawn during submission")
            return {"id": "remote-still-needs-cancellation"}
    result = dispatch(workspace, post["id"], ConcurrentBuffer())
    assert result["status"] == "blocked"
    assert result["buffer_post_id"] == "remote-still-needs-cancellation"
    assert "Cancela" in result["buffer_note"]


def test_daily_plan_counts_previously_reserved_posts(workspace):
    workspace.settings({"daily_x": 1})
    day = (ed.now() + timedelta(days=1)).date().isoformat()
    approved(workspace)
    assert len(workspace.day_plan(day)) == 1
    approved(workspace, title="Otra pregunta", text="¿Cuál es tu plan?")
    assert workspace.day_plan(day) == []


def test_clip_request_respects_avoided_creators(workspace, tmp_path):
    target = tmp_path / "input.mp4"
    target.write_bytes(b"video")
    source = workspace.source({"name": "Not this creator", "url": "https://youtube.com/@avoided", "kind": "creator", "credit": "@avoided", "permission": "own", "permission_note": "Own recording"})
    workspace.settings({"avoid_creators": "@avoided"})
    with pytest.raises(ValueError, match="evitar"):
        clipping.request(workspace, {"target": str(target), "source_id": source["id"], "commentary": "Test"})
