"""Whole-video API wiring and progress without downloading or publishing."""

import json
import os
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "webapp" / "api"))

import clips_api  # noqa: E402


URL = "https://www.youtube.com/watch?v=abcdefghijk"


@pytest.fixture
def endpoint(monkeypatch):
    from sse_starlette.sse import AppStatus

    AppStatus.should_exit_event = None
    calls = []

    def spawn(args, title="", **kwargs):
        calls.append({"args": args, "title": title, **kwargs})
        return object()

    async def stream(job):
        yield {"event": "done", "data": "{}"}

    monkeypatch.setattr(clips_api, "_spawn_job", spawn)
    monkeypatch.setattr(clips_api, "_stream_job", stream)
    app = FastAPI()
    app.include_router(clips_api.router)
    return TestClient(app), calls


def test_full_video_defaults_process_all_parts_without_the_auto_count_cap(endpoint):
    client, calls = endpoint
    response = client.get("/api/clips/run", params={"target": URL, "mode": "full", "count": 99})
    assert response.status_code == 200
    args = calls[0]["args"]
    assert args[:3] == ["clips", "full", URL]
    assert args[args.index("--clip-seconds") + 1] == "60.0"
    assert args[args.index("--batch-size") + 1] == "0"
    assert "--count" not in args and "--no-resume" not in args
    assert "channel_id" not in calls[0] and "kind" not in calls[0]
    assert "video completo" in calls[0]["title"]


def test_full_video_passes_batch_and_render_options_without_uploading(endpoint):
    client, calls = endpoint
    response = client.get("/api/clips/run", params={
        "target": URL, "mode": "full", "clip_seconds": 90, "batch_size": 25,
        "resume": "false", "layout": "crop", "subtitles": "false", "whisper": "true",
        "language": "es", "rights": "mi video",
    })
    assert response.status_code == 200
    args = calls[0]["args"]
    assert args[args.index("--clip-seconds") + 1] == "90.0"
    assert args[args.index("--batch-size") + 1] == "25"
    assert args[args.index("--layout") + 1] == "crop"
    assert args[args.index("--language") + 1] == "es"
    assert args[args.index("--rights") + 1] == "mi video"
    assert {"--no-resume", "--no-subtitles", "--whisper"}.issubset(args)
    assert "upload" not in args and "--channel-id" not in args


@pytest.mark.parametrize("changes", [
    {"clip_seconds": 4.99}, {"clip_seconds": 181}, {"clip_seconds": "nan"},
    {"clip_seconds": "inf"}, {"batch_size": -1},
])
def test_bad_full_video_lengths_are_refused_before_spawning(endpoint, changes):
    client, calls = endpoint
    response = client.get("/api/clips/run", params={"target": URL, "mode": "full", **changes})
    assert response.status_code == 400
    assert calls == []


def test_full_video_accepts_an_existing_local_source(endpoint, tmp_path):
    client, calls = endpoint
    source = tmp_path / "mi video.mp4"
    source.write_bytes(b"local source")
    response = client.get("/api/clips/run", params={"target": str(source), "mode": "full"})
    assert response.status_code == 200
    assert calls[0]["args"][:3] == ["clips", "full", str(source)]


def _saved_plan(directory, *, sequence_id="sequence-new", signature="new-source", accept_legacy=False):
    directory.mkdir(parents=True, exist_ok=True)
    plan = {
        "source_id": "source-1", "duration": 125, "clip_seconds": 60,
        "spans": [[0, 60], [60, 120], [120, 125]], "sequence_id": sequence_id,
        "source_signature": signature, "accept_legacy": accept_legacy,
    }
    path = directory / "sequence-60.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    return path


def _record(tmp_path, name, start, end, **changes):
    path = tmp_path / f"{name}.mp4"
    path.write_bytes(b"rendered clip")
    return {
        "id": name, "source_id": "source-1", "path": str(path), "status": "draft",
        "start": start, "end": end, "source_signature": "new-source",
        "sequence_id": "sequence-new", **changes,
    }


def test_source_progress_keeps_an_earlier_hole_even_when_a_later_part_exists(tmp_path):
    directory = tmp_path / "sources" / "source-1"
    _saved_plan(directory)
    first = _record(tmp_path, "first", 0, 60)
    missing = _record(tmp_path, "missing", 60, 120)
    Path(missing["path"]).unlink()
    last = _record(tmp_path, "last", 120, 125)
    progress = clips_api._source_sequence(directory, "source-1", [first, missing, last])
    assert progress == {
        "total_parts": 3, "completed_parts": 2, "remaining_parts": 1,
        "next_start": 60, "complete": False, "clip_seconds": 60, "sequence_id": "sequence-new",
    }


def test_source_progress_excludes_other_source_versions_and_rejected_parts(tmp_path):
    directory = tmp_path / "sources" / "source-1"
    _saved_plan(directory)
    old = _record(tmp_path, "old", 0, 125, source_signature="old-source", sequence_id="old-plan")
    rejected = _record(tmp_path, "rejected", 0, 125, status="rejected")
    assert clips_api._source_sequence(directory, "source-1", [old, rejected])["completed_parts"] == 0


def test_isolated_new_series_does_not_reuse_complete_old_series_with_the_same_source(tmp_path):
    directory = tmp_path / "sources" / "source-1"
    path = _saved_plan(directory, accept_legacy=True)
    plan = json.loads(path.read_text(encoding="utf-8"))
    plan["isolated_sequence"] = True
    path.write_text(json.dumps(plan), encoding="utf-8")
    new_part = _record(tmp_path, "new-first", 0, 60)
    old_series = _record(tmp_path, "old-complete", 0, 125, sequence_id="old-series")
    old_manual = _record(tmp_path, "old-manual", 0, 125, sequence_id=None)
    progress = clips_api._source_sequence(directory, "source-1", [old_series, old_manual, new_part])
    assert progress["completed_parts"] == 1
    assert progress["remaining_parts"] == 2 and progress["next_start"] == 60
    assert progress["complete"] is False


@pytest.mark.parametrize("change", ["content", "mtime", "missing"])
def test_altered_local_source_invalidates_old_complete_progress_and_sources_response(monkeypatch, tmp_path, change):
    root = tmp_path / "sources"
    directory = root / "source-1"
    plan_path = _saved_plan(directory)
    local = tmp_path / "original.mp4"
    local.write_bytes(b"original source")
    stat = local.stat()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["identity"] = {"path": str(local.resolve()), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    completed = _record(tmp_path, "old-all-parts", 0, 125, source_path=str(local), source_duration_seconds=125)
    monkeypatch.setattr(clips_api.clips_config, "sources_dir", lambda: str(root))
    monkeypatch.setattr(clips_api.clips_store, "listing", lambda: [completed])
    app = FastAPI()
    app.include_router(clips_api.router)
    client = TestClient(app)
    before = client.get("/api/clips/meta/sources")
    assert before.status_code == 200 and before.json()[0]["sequence"]["complete"] is True

    if change == "content":
        local.write_bytes(b"replacement source at the same path with a different size")
    elif change == "mtime":
        os.utime(local, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    else:
        local.unlink()

    assert Path(completed["path"]).is_file()
    assert clips_api._source_sequence(directory, "source-1", [completed]) is None
    after = client.get("/api/clips/meta/sources")
    assert after.status_code == 200
    assert after.json()[0]["target"] == str(local)
    assert after.json()[0]["sequence"] is None


def test_source_progress_can_use_legacy_manual_coverage_as_a_union(tmp_path):
    directory = tmp_path / "sources" / "source-1"
    _saved_plan(directory, accept_legacy=True)
    records = [
        _record(tmp_path, "left", 0, 30, sequence_id=None, source_signature=None),
        _record(tmp_path, "right", 30, 60, sequence_id=None, source_signature=None),
    ]
    progress = clips_api._source_sequence(directory, "source-1", records)
    assert progress["completed_parts"] == 1 and progress["next_start"] == 60


def test_latest_saved_duration_plan_drives_the_source_progress(tmp_path):
    directory = tmp_path / "sources" / "source-1"
    first = _saved_plan(directory)
    newer = directory / "sequence-30.json"
    data = json.loads(first.read_text(encoding="utf-8"))
    data.update(clip_seconds=30, sequence_id="sequence-30", spans=[[0, 30], [30, 60], [60, 90], [90, 120], [120, 125]])
    newer.write_text(json.dumps(data), encoding="utf-8")
    os.utime(first, (100, 100))
    os.utime(newer, (200, 200))
    progress = clips_api._source_sequence(directory, "source-1", [])
    assert progress["total_parts"] == 5 and progress["clip_seconds"] == 30


def test_sources_include_local_continuation_targets_and_saved_progress(monkeypatch, tmp_path):
    root = tmp_path / "sources"
    _saved_plan(root / "source-1")
    local = tmp_path / "original.mp4"
    local.write_bytes(b"source")
    record = _record(tmp_path, "first", 0, 60, source_path=str(local), source_title="Mi video", source_duration_seconds=125)
    monkeypatch.setattr(clips_api.clips_config, "sources_dir", lambda: str(root))
    monkeypatch.setattr(clips_api.clips_store, "listing", lambda: [record])
    sources = clips_api.sources()
    assert len(sources) == 1
    assert sources[0]["target"] == str(local)
    assert sources[0]["duration_seconds"] == 125
    assert sources[0]["sequence"]["completed_parts"] == 1


def test_clip_response_exposes_stable_part_number_and_local_source_target(monkeypatch, tmp_path):
    monkeypatch.setattr(clips_api.clips_config, "clips_dir", lambda: str(tmp_path))
    record = _record(tmp_path, "part", 60, 120, source_path=str(tmp_path / "original.mp4"), sequence_index=2, sequence_total=3, clip_seconds=60)
    output = clips_api._clip_out(record)
    assert output["source_target"] == record["source_path"]
    assert (output["sequence_index"], output["sequence_total"], output["clip_seconds"]) == (2, 3, 60)
    assert output["available"] is True
