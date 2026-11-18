"""Clip uploads start once by POST; event reconnections only observe that job."""

import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "webapp" / "api"))

import clips_api  # noqa: E402
from clips import upload as clips_upload  # noqa: E402


@pytest.fixture
def endpoint(monkeypatch, tmp_path):
    from sse_starlette.sse import AppStatus

    AppStatus.should_exit_event = None
    clip_id = uuid.uuid4().hex[:12]
    clip_path = tmp_path / f"{clip_id}.mp4"
    clip_path.write_bytes(b"local video")
    profile = tmp_path / "firefox"
    profile.mkdir()
    clip = {
        "id": clip_id, "title": "Título original", "path": str(clip_path),
        "status": "draft", "duration": 30, "source_title": "Fuente",
    }
    channel = {"id": "channel-1", "nickname": "Mi canal", "firefox_profile": str(profile)}
    spawned = []
    observed = []
    validated = []
    reconciled = []

    def validate(clip_id, channel_id, title=None, description=None):
        validated.append((clip_id, channel_id, title, description))
        return {
            "clip": clip, "channel": channel, "profile": str(profile),
            "path": str(clip_path), "title": title or clip["title"],
            "description": description or "Descripción preparada #Shorts",
        }

    def spawn(args, title="", **kwargs):
        job = SimpleNamespace(id=uuid.uuid4().hex[:12], status="running", rc=None)
        spawned.append({"args": args, "title": title, "job": job, **kwargs})
        return job

    async def stream(job):
        observed.append(job)
        yield {"event": "done", "data": "{}"}

    monkeypatch.setattr(clips_api.clips_config, "clips_dir", lambda: str(tmp_path))
    monkeypatch.setattr(clips_api.clips_store, "load", lambda: {"clips": [clip]})
    monkeypatch.setattr(clips_upload, "validate_upload", validate)
    monkeypatch.setattr(clips_upload, "reconcile_uploads", lambda: reconciled.append(True), raising=False)
    monkeypatch.setattr(clips_api, "_spawn_job", spawn)
    monkeypatch.setattr(clips_api, "_stream_job", stream)
    monkeypatch.setattr(clips_api, "_UPLOAD_JOBS", {})
    monkeypatch.setattr(clips_api, "_CLIP_UPLOAD_JOBS", {})
    app = FastAPI()
    app.include_router(clips_api.router)
    return SimpleNamespace(
        client=TestClient(app), clip=clip, channel=channel,
        spawned=spawned, observed=observed, validated=validated, reconciled=reconciled,
    )


def start(endpoint, **changes):
    return endpoint.client.post(
        f"/api/clips/{endpoint.clip['id']}/upload",
        json={"channel_id": endpoint.channel["id"], **changes},
    )


def test_post_upload_uses_the_exact_clip_and_channel_with_reviewed_metadata(endpoint):
    title = 'Un título con "comillas"'
    description = "Primera línea\nSegunda línea con $(texto literal) #Shorts"
    response = start(endpoint, title=title, description=description)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result == {
        "job_id": endpoint.spawned[0]["job"].id,
        "stream_url": f"/api/clips/uploads/{endpoint.spawned[0]['job'].id}/events",
    }
    assert endpoint.validated == [(endpoint.clip["id"], endpoint.channel["id"], title, description)]
    args = endpoint.spawned[0]["args"]
    assert args[:3] == ["clips", "upload", endpoint.clip["id"]]
    assert args[args.index("--channel-id") + 1] == endpoint.channel["id"]
    assert args[args.index("--title") + 1] == title
    assert args[args.index("--description") + 1] == description
    assert "channel_id" not in endpoint.spawned[0] and "kind" not in endpoint.spawned[0]
    assert endpoint.observed == []


def test_upload_events_can_reconnect_without_starting_another_upload(endpoint):
    response = start(endpoint)
    assert response.status_code == 200, response.text
    url = response.json()["stream_url"]
    # One application lifespan keeps both SSE connections on the same
    # loop, as in production (sse_starlette's exit event is loop-bound).
    with endpoint.client as client:
        first = client.get(url)
        second = client.get(url)
    assert first.status_code == second.status_code == 200
    assert "event: done" in first.text and "event: done" in second.text
    assert len(endpoint.spawned) == 1
    assert endpoint.observed == [endpoint.spawned[0]["job"]] * 2


def test_double_click_does_not_create_a_second_upload_job(endpoint):
    assert start(endpoint).status_code == 200
    response = start(endpoint)
    assert response.status_code == 409
    assert len(endpoint.spawned) == 1


def test_unknown_upload_event_stream_does_not_spawn_a_job(endpoint):
    response = endpoint.client.get("/api/clips/uploads/does-not-exist/events")
    assert response.status_code == 404
    assert endpoint.spawned == []


def test_upload_creation_is_not_available_by_get(endpoint):
    response = endpoint.client.get(f"/api/clips/{endpoint.clip['id']}/upload")
    assert response.status_code == 405
    assert endpoint.spawned == []


def test_missing_clip_fails_before_validation_or_spawning(endpoint):
    response = endpoint.client.post(
        "/api/clips/missing/upload", json={"channel_id": "channel-1"},
    )
    assert response.status_code == 404
    assert endpoint.spawned == [] and endpoint.validated == []


def test_profile_or_upload_validation_failure_never_spawns_firefox(endpoint, monkeypatch):
    def invalid(*args, **kwargs):
        raise clips_upload.UploadError("El perfil de Firefox no existe")

    monkeypatch.setattr(clips_upload, "validate_upload", invalid)
    response = start(endpoint)
    assert response.status_code == 400
    assert "Firefox" in response.json()["detail"]
    assert endpoint.spawned == []


@pytest.mark.parametrize("changes", [{"title": "x" * 101}, {"description": "x" * 5001}])
def test_youtube_metadata_limits_are_validated_before_spawning(endpoint, changes):
    response = start(endpoint, **changes)
    assert response.status_code == 422
    assert endpoint.spawned == []


def test_catalogue_includes_upload_status_and_link(endpoint):
    endpoint.clip["youtube_upload"] = {
        "status": "uploaded", "channel_id": "channel-1",
        "channel_nickname": "Mi canal", "url": "https://www.youtube.com/shorts/abc123",
    }
    response = endpoint.client.get("/api/clips")
    assert response.status_code == 200
    assert response.json()[0]["youtube_upload"] == endpoint.clip["youtube_upload"]


def test_listing_reconciles_uploads_after_an_interrupted_worker(endpoint):
    response = endpoint.client.get("/api/clips")
    assert response.status_code == 200
    assert endpoint.reconciled == [True]


def test_confirming_a_published_upload_records_the_reviewed_video_url(endpoint, monkeypatch):
    calls = []
    url = "https://www.youtube.com/shorts/abcdefghijk"
    resolved = {**endpoint.clip, "status": "published", "youtube_upload": {
        "status": "uploaded", "url": url, "channel_id": endpoint.channel["id"],
    }}

    def resolve(clip_id, action, url=None):
        calls.append((clip_id, action, url))
        return resolved

    monkeypatch.setattr(clips_upload, "resolve_upload", resolve, raising=False)
    response = endpoint.client.post(
        f"/api/clips/{endpoint.clip['id']}/upload-resolution",
        json={"action": "published", "url": url},
    )
    assert response.status_code == 200, response.text
    assert calls == [(endpoint.clip["id"], "published", url)]
    assert response.json()["status"] == "published"
    assert response.json()["youtube_upload"]["url"] == url
    assert endpoint.spawned == []


def test_allowing_a_retry_only_updates_the_state_without_starting_an_upload(endpoint, monkeypatch):
    calls = []

    def resolve(clip_id, action, url=None):
        calls.append((clip_id, action, url))
        return {**endpoint.clip, "youtube_upload": {"status": "failed", "error": "Intento revisado"}}

    monkeypatch.setattr(clips_upload, "resolve_upload", resolve, raising=False)
    response = endpoint.client.post(
        f"/api/clips/{endpoint.clip['id']}/upload-resolution", json={"action": "retry"},
    )
    assert response.status_code == 200, response.text
    assert len(calls) == 1 and calls[0][:2] == (endpoint.clip["id"], "retry")
    assert response.json()["youtube_upload"]["status"] == "failed"
    assert endpoint.spawned == []


def test_upload_resolution_rejects_an_unknown_action(endpoint):
    response = endpoint.client.post(
        f"/api/clips/{endpoint.clip['id']}/upload-resolution", json={"action": "upload-again"},
    )
    assert response.status_code == 422
    assert endpoint.spawned == []


def test_upload_resolution_preserves_service_validation_errors(endpoint, monkeypatch):
    def invalid(*args, **kwargs):
        raise clips_upload.UploadError("Pega la URL válida del video publicado en YouTube")

    monkeypatch.setattr(clips_upload, "resolve_upload", invalid, raising=False)
    response = endpoint.client.post(
        f"/api/clips/{endpoint.clip['id']}/upload-resolution",
        json={"action": "published", "url": "https://studio.youtube.com"},
    )
    assert response.status_code == 400
    assert "URL" in response.json()["detail"]
    assert endpoint.spawned == []


def test_missing_clip_cannot_be_resolved(endpoint, monkeypatch):
    calls = []
    monkeypatch.setattr(clips_upload, "resolve_upload", lambda *args: calls.append(args), raising=False)
    response = endpoint.client.post("/api/clips/missing/upload-resolution", json={"action": "retry"})
    assert response.status_code == 404
    assert calls == [] and endpoint.spawned == []


def test_job_system_must_be_ready_before_starting_upload(endpoint, monkeypatch):
    monkeypatch.setattr(clips_api, "_spawn_job", None)
    response = start(endpoint)
    assert response.status_code == 503
    assert endpoint.spawned == []


def test_upload_channels_show_the_effective_firefox_profile_and_readiness(endpoint, monkeypatch, tmp_path):
    import cache

    fallback = tmp_path / "default-firefox"
    fallback.mkdir()
    channels = [
        endpoint.channel,
        {"id": "channel-global", "nickname": "Global", "firefox_profile": ""},
        {"id": "channel-missing", "nickname": "Sin perfil", "firefox_profile": str(tmp_path / "missing")},
    ]
    monkeypatch.setattr(cache, "get_accounts", lambda provider: channels if provider == "youtube" else [])
    if hasattr(clips_api, "get_accounts"):
        monkeypatch.setattr(clips_api, "get_accounts", cache.get_accounts)
    if hasattr(clips_upload, "get_accounts"):
        monkeypatch.setattr(clips_upload, "get_accounts", cache.get_accounts)
    monkeypatch.setattr(clips_upload, "resolve_profile", lambda channel: channel.get("firefox_profile") or str(fallback))
    response = endpoint.client.get("/api/clips/meta/upload-channels")
    assert response.status_code == 200, response.text
    rows = {row["id"]: row for row in response.json()}
    assert rows[endpoint.channel["id"]]["firefox_profile"] == endpoint.channel["firefox_profile"]
    assert rows[endpoint.channel["id"]]["profile_ready"] is True
    assert rows["channel-global"]["firefox_profile"] == str(fallback)
    assert rows["channel-global"]["profile_ready"] is True
    assert rows["channel-missing"]["profile_ready"] is False
