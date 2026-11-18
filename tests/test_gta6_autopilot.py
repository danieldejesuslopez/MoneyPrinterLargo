"""Watchlist scheduling, permissions and crash recovery without network/rendering."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gta6 import Workspace, autopilot, clipping, discovery, editorial as ed
from gta6.service import find
from clips import store as clips_store


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(ed, "now", lambda: datetime(2026, 11, 17, 12, tzinfo=timezone.utc))
    monkeypatch.setattr(clips_store, "listing", lambda *args, **kwargs: [])
    return Workspace(tmp_path / "gta6")


def source(workspace, name="Creator", permission="granted", **extra):
    return workspace.source({"name": name, "url": f"https://www.youtube.com/@{name}",
                             "kind": "creator", "credit": f"@{name}", "permission": permission,
                             "permission_note": "Permiso documentado" if permission == "granted" else "",
                             **extra})


def video(number=1, **extra):
    return {"video_id": f"gta{number:08d}", "url": f"https://www.youtube.com/watch?v=gta{number:08d}",
            "title": "GTA V Online carreras", "published_at": (ed.now() - timedelta(hours=1)).isoformat(),
            "duration": 300, "is_live": False, **extra}


def discover(monkeypatch, videos):
    monkeypatch.setattr(discovery, "discover_source", lambda *args, **kwargs: {"status": "ok", "videos": videos, "error": ""})


def fake_renderer(monkeypatch):
    calls = []

    def render(workspace, request_id, on_status=print):
        calls.append(request_id)
        with workspace.store.edit() as state:
            job = find(state["clip_jobs"], request_id)
            clip_id, post_id = "clip-" + request_id, "post-" + request_id
            job.update(status="done", clip_ids=[clip_id], post_ids=[post_id])
            state["posts"].append({"id": post_id, "status": "draft", "clip_id": clip_id})
        return {"clips": [{"id": clip_id}], "posts": [{"id": post_id}], "failures": []}

    monkeypatch.setattr(clipping, "run", render)
    return calls, render


def test_scan_discovers_but_never_grants_permission(workspace, monkeypatch):
    creator = source(workspace, permission="review")
    discover(monkeypatch, [video()])
    calls, _ = fake_renderer(monkeypatch)
    autopilot.configure(workspace, {"enabled": True})
    result = autopilot.run(workspace)
    assert result["candidates"][0]["status"] == "needs_permission"
    assert result["stats"]["pending_permission"] == 1
    assert workspace.store.read()["sources"][-1]["permission"] == "review"
    assert not calls
    assert result["candidates"][0]["source_id"] == creator["id"]


def test_scan_rejects_undated_old_future_live_and_unrelated_videos(workspace, monkeypatch):
    source(workspace)
    discover(monkeypatch, [video(1, published_at=""), video(2, published_at=(ed.now() - timedelta(days=8)).isoformat()),
                           video(3, published_at=(ed.now() + timedelta(hours=1)).isoformat()),
                           video(4, is_live=True), video(5, title="Minecraft survival"),
                           video(6, title="GTA 6 leaked gameplay"), video(7)])
    autopilot.configure(workspace, {"videos_per_source": 10})
    result = autopilot.run(workspace, produce=False)
    assert [c["url"] for c in result["candidates"]] == [video(7)["url"]]


def test_rotating_batches_isolate_source_errors_and_skip_blocked_sources(workspace, monkeypatch):
    source(workspace, "One")
    source(workspace, "Two")
    source(workspace, "Three")
    source(workspace, "Denied", permission="denied")
    source(workspace, "Disabled", active=False)
    source(workspace, "Cards", monitor_enabled=False)
    source(workspace, "Avoid")
    workspace.settings({"avoid_creators": "Avoid"})
    visited = []

    def scan(row, **kwargs):
        visited.append(row["name"])
        if row["name"] == "One":
            raise RuntimeError("temporary outage")
        return {"status": "ok", "videos": [video(2 if row["name"] == "Two" else 3)]}

    monkeypatch.setattr(discovery, "discover_source", scan)
    autopilot.configure(workspace, {"sources_per_scan": 2})
    first = autopilot.run(workspace, produce=False)
    assert len(first["candidates"]) == 1
    assert first["scans"][-1]["sources"][0]["status"] == "error"
    autopilot.run(workspace, produce=False)
    assert visited == ["One", "Two", "Three", "One"]


def test_dedup_survives_restart_and_url_variants(workspace, monkeypatch):
    source(workspace, "One")
    source(workspace, "Two")
    discover(monkeypatch, [video(), video(url="https://youtu.be/gta00000001?si=tracking")])
    calls, _ = fake_renderer(monkeypatch)
    autopilot.configure(workspace, {"enabled": True})
    autopilot.run(workspace)
    result = autopilot.run(Workspace(workspace.store.directory))
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["status"] == "done"
    assert len(calls) == 1
    assert len(workspace.store.read()["clip_jobs"]) == 1
    assert workspace.store.read()["posts"][0]["status"] == "draft"


def test_existing_candidate_unlocks_after_permission_change_without_rediscovery(workspace, monkeypatch):
    creator = source(workspace, permission="review")
    discover(monkeypatch, [video()])
    autopilot.run(workspace, produce=False)
    workspace.source({"permission": "granted", "permission_note": "Creator authorization"}, creator["id"])
    discover(monkeypatch, [])
    calls, _ = fake_renderer(monkeypatch)
    autopilot.configure(workspace, {"enabled": True})
    result = autopilot.run(workspace)
    assert result["candidates"][0]["status"] == "done"
    assert len(calls) == 1


def test_paused_manual_scan_and_zero_budget_do_not_produce(workspace, monkeypatch):
    source(workspace)
    discover(monkeypatch, [video()])
    calls, _ = fake_renderer(monkeypatch)
    assert autopilot.run(workspace)["candidates"][0]["status"] == "ready"
    autopilot.configure(workspace, {"enabled": True, "max_productions_per_day": 0})
    autopilot.run(workspace)
    assert not calls


def test_pause_during_render_stops_next_production(workspace, monkeypatch):
    source(workspace)
    discover(monkeypatch, [video(1), video(2)])
    calls, render = fake_renderer(monkeypatch)

    def pause(workspace, request_id, **kwargs):
        result = render(workspace, request_id, **kwargs)
        autopilot.configure(workspace, {"enabled": False})
        return result

    monkeypatch.setattr(clipping, "run", pause)
    autopilot.configure(workspace, {"enabled": True})
    result = autopilot.run(workspace)
    assert len(calls) == 1
    assert result["stats"]["counts"] == {"done": 1, "ready": 1}
    assert not result["next_scan_at"]


def test_permission_revocation_during_render_blocks_next_source(workspace, monkeypatch):
    source(workspace, "One")
    second = source(workspace, "Two")
    monkeypatch.setattr(discovery, "discover_source", lambda s, **kwargs: {"status": "ok", "videos": [video(1 if s["name"] == "One" else 2)]})
    calls, render = fake_renderer(monkeypatch)

    def revoke(workspace, request_id, **kwargs):
        result = render(workspace, request_id, **kwargs)
        workspace.source({"permission": "denied"}, second["id"])
        return result

    monkeypatch.setattr(clipping, "run", revoke)
    autopilot.configure(workspace, {"enabled": True})
    result = autopilot.run(workspace)
    assert len(calls) == 1
    assert result["candidates"][1]["status"] == "needs_permission"


def test_attempts_are_capped_and_reset_in_workspace_timezone(workspace, monkeypatch):
    current = [datetime(2026, 11, 18, 3, 30, tzinfo=timezone.utc)]
    monkeypatch.setattr(ed, "now", lambda: current[0])
    source(workspace)
    discover(monkeypatch, [video(1), video(2)])
    calls, _ = fake_renderer(monkeypatch)
    autopilot.configure(workspace, {"enabled": True, "max_productions_per_day": 1})
    autopilot.run(workspace)
    autopilot.run(workspace)
    assert len(calls) == 1
    assert workspace.store.read()["autopilot"]["production_attempts"][0]["day"] == "2026-11-17"
    current[0] += timedelta(hours=1)
    result = autopilot.run(workspace)
    assert len(calls) == 2
    assert result["stats"]["produced_today"] == 1


def test_failed_request_consumes_budget_without_automatic_retry(workspace, monkeypatch):
    source(workspace)
    discover(monkeypatch, [video(1), video(2)])
    calls = []

    def fail(workspace, request_id, **kwargs):
        calls.append(request_id)
        with workspace.store.edit() as state:
            find(state["clip_jobs"], request_id).update(status="error", error="encoder unavailable")
        raise ValueError("encoder unavailable")

    monkeypatch.setattr(clipping, "run", fail)
    autopilot.configure(workspace, {"enabled": True, "max_productions_per_day": 1})
    first = autopilot.run(workspace)
    assert first["candidates"][0]["status"] == "error"
    autopilot.run(workspace)
    assert len(calls) == 1
    assert autopilot.snapshot(workspace)["stats"]["produced_today"] == 1


def test_completed_request_reconciles_after_process_crash(workspace, monkeypatch):
    source(workspace)
    discover(monkeypatch, [video()])
    calls, render = fake_renderer(monkeypatch)

    def crash_after_done(workspace, request_id, **kwargs):
        render(workspace, request_id, **kwargs)
        raise SystemExit("worker stopped")

    monkeypatch.setattr(clipping, "run", crash_after_done)
    autopilot.configure(workspace, {"enabled": True})
    with pytest.raises(SystemExit):
        autopilot.run(workspace)
    result = autopilot.run(Workspace(workspace.store.directory))
    assert len(calls) == 1
    assert result["candidates"][0]["status"] == "done"
    assert result["candidates"][0]["post_ids"]


def test_interrupted_render_requires_explicit_retry_and_reuses_same_request(workspace, monkeypatch):
    source(workspace)
    discover(monkeypatch, [video()])
    calls, render = fake_renderer(monkeypatch)

    def interrupt(workspace, request_id, **kwargs):
        with workspace.store.edit() as state:
            find(state["clip_jobs"], request_id)["status"] = "running"
        raise SystemExit("process killed")

    monkeypatch.setattr(clipping, "run", interrupt)
    autopilot.configure(workspace, {"enabled": True, "max_productions_per_day": 3})
    with pytest.raises(SystemExit):
        autopilot.run(workspace)
    row = autopilot.run(workspace)["candidates"][0]
    assert row["status"] == "error"
    request_id = row["request_id"]
    autopilot.retry(workspace, row["id"])
    monkeypatch.setattr(clipping, "run", render)
    result = autopilot.run(workspace)
    assert result["candidates"][0]["status"] == "done"
    assert calls == [request_id]
    assert len(workspace.store.read()["clip_jobs"]) == 1


def test_retry_refuses_to_duplicate_partial_media(workspace, monkeypatch):
    source(workspace)
    discover(monkeypatch, [video()])
    autopilot.run(workspace, produce=False)
    with workspace.store.edit() as state:
        row = state["autopilot"]["candidates"][0]
        row.update(status="error", request_id="failed-job")
        state["clip_jobs"] = [{"id": "failed-job", "status": "error"}]
        candidate_id = row["id"]
    monkeypatch.setattr(clips_store, "listing", lambda: [{"id": "rendered", "source_url": video()["url"]}])
    with pytest.raises(ValueError, match="Ya hay clips"):
        autopilot.retry(workspace, candidate_id)


def test_existing_manual_clip_is_not_rendered_again_or_charged_to_budget(workspace, monkeypatch):
    source(workspace)
    discover(monkeypatch, [video(1), video(2)])
    calls, _ = fake_renderer(monkeypatch)
    monkeypatch.setattr(clips_store, "listing", lambda: [{"id": "manual", "source_url": "https://youtu.be/gta00000001?si=tracking"}])
    autopilot.configure(workspace, {"enabled": True, "max_productions_per_day": 1})
    result = autopilot.run(workspace)
    assert result["candidates"][0]["status"] == "ignored"
    assert "catálogo" in result["candidates"][0]["error"]
    assert result["candidates"][1]["status"] == "done"
    assert len(calls) == 1
    assert result["stats"]["produced_today"] == 1


def test_estimated_publication_date_is_preserved_for_review(workspace, monkeypatch):
    source(workspace, permission="review")
    discover(monkeypatch, [video(published_at_estimated=True)])
    result = autopilot.run(workspace, produce=False)
    assert result["candidates"][0]["published_at_estimated"] is True


@pytest.mark.parametrize("field", ["url", "monitor_url"])
def test_source_identity_change_during_query_discards_old_channel_results(workspace, monkeypatch, field):
    creator = source(workspace)

    def scan(row, **kwargs):
        workspace.source({field: "https://www.youtube.com/@DifferentCreator"}, creator["id"])
        return {"status": "ok", "videos": [video()]}

    monkeypatch.setattr(discovery, "discover_source", scan)
    calls, _ = fake_renderer(monkeypatch)
    autopilot.configure(workspace, {"enabled": True})
    result = autopilot.run(workspace)
    assert not result["candidates"]
    assert not calls
    assert result["scans"][-1]["sources"][0]["status"] == "error"
    assert "identidad" in result["scans"][-1]["sources"][0]["error"]


def test_source_identity_change_before_production_ignores_original_candidate(workspace, monkeypatch):
    creator = source(workspace)
    discover(monkeypatch, [video()])
    first = autopilot.run(workspace, produce=False)
    original_identity = first["candidates"][0]["source_identity"]
    workspace.source({"monitor_url": "https://www.youtube.com/@DifferentCreator"}, creator["id"])
    calls, _ = fake_renderer(monkeypatch)
    autopilot.configure(workspace, {"enabled": True})
    result = autopilot.run(workspace)
    assert result["candidates"][0]["status"] == "ignored"
    assert result["candidates"][0]["source_identity"] == original_identity
    assert result["candidates"][0]["source_id"] == creator["id"]
    assert not calls
    assert not result["production_attempts"]


def test_identity_change_between_renders_cannot_use_new_channel_permission(workspace, monkeypatch):
    source(workspace, "One")
    second = source(workspace, "Two")
    monkeypatch.setattr(discovery, "discover_source", lambda s, **kwargs: {"status": "ok", "videos": [video(1 if s["name"] == "One" else 2)]})
    calls, render = fake_renderer(monkeypatch)

    def edit_second(workspace, request_id, **kwargs):
        result = render(workspace, request_id, **kwargs)
        workspace.source({"url": "https://www.youtube.com/@DifferentCreator"}, second["id"])
        return result

    monkeypatch.setattr(clipping, "run", edit_second)
    autopilot.configure(workspace, {"enabled": True})
    result = autopilot.run(workspace)
    assert len(calls) == 1
    assert result["candidates"][1]["status"] == "ignored"
    assert result["stats"]["produced_today"] == 1


def test_overlapping_workers_and_retry_are_rejected(workspace, monkeypatch):
    with autopilot._lock(workspace):
        assert autopilot.snapshot(workspace)["running"]
        with pytest.raises(BlockingIOError):
            autopilot.run(workspace, produce=False)
        with pytest.raises(BlockingIOError):
            autopilot.retry(workspace, "anything")
    assert not autopilot.snapshot(workspace)["running"]


def test_ignore_is_durable_and_cannot_be_rediscovered(workspace, monkeypatch):
    source(workspace)
    discover(monkeypatch, [video()])
    result = autopilot.run(workspace, produce=False)
    row = autopilot.ignore(workspace, result["candidates"][0]["id"])
    assert row["status"] == "ignored"
    calls, _ = fake_renderer(monkeypatch)
    autopilot.configure(workspace, {"enabled": True})
    assert len(autopilot.run(workspace)["candidates"]) == 1
    assert not calls


@pytest.mark.parametrize("changes", [{"sources_per_scan": True}, {"interval_minutes": 0},
                                    {"max_seconds": float("nan")}, {"enabled": "yes"},
                                    {"commentary": " "}, {"unknown": 1}])
def test_config_rejects_invalid_values_atomically(workspace, changes):
    before = autopilot.snapshot(workspace)["config"]
    with pytest.raises(ValueError):
        autopilot.configure(workspace, changes)
    assert autopilot.snapshot(workspace)["config"] == before


def test_schedule_and_scan_history_remain_bounded(workspace, monkeypatch):
    discover(monkeypatch, [])
    autopilot.configure(workspace, {"enabled": True, "interval_minutes": 30})
    for _ in range(22):
        result = autopilot.run(workspace, produce=False)
    assert len(result["scans"]) == 20
    assert ed.parse_time(result["next_scan_at"]) == ed.now() + timedelta(minutes=30)
    assert result["dispatch_until"] == ""


def test_restart_marks_previous_running_scan_as_interrupted(workspace, monkeypatch):
    discover(monkeypatch, [])
    with workspace.store.edit() as state:
        state["autopilot"]["scans"].append({"id": "abandoned", "status": "running", "finished_at": ""})
    result = autopilot.run(workspace, produce=False)
    previous = result["scans"][0]
    assert previous["status"] == "error"
    assert previous["finished_at"]
    assert "interrumpido" in previous["error"]
    assert result["scans"][-1]["status"] == "done"
