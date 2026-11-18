"""Whole-video batches resume without gaps, duplicates or any upload work."""

import hashlib
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import clips  # noqa: E402
from clips import config as clips_config  # noqa: E402
from clips import ingest, pipeline, render, store, transcribe  # noqa: E402


@pytest.fixture
def full_video(tmp_path, monkeypatch):
    monkeypatch.setattr(clips_config, "root_dir", lambda: str(tmp_path))
    media = tmp_path / "original.mp4"
    media.write_bytes(b"source media")
    source = ingest.Source(
        id="source-1", path=str(media), title="La partida completa",
        duration_seconds=125, rights_note="local file", language="es",
    )
    calls = []
    ingests = []
    statuses = []
    fail_at = SimpleNamespace(index=None)

    def resolve(target, rights_note, **kwargs):
        ingests.append((target, rights_note, kwargs))
        return source

    def render_clip(path, start, duration, out_path, **kwargs):
        calls.append({"source": path, "start": start, "duration": duration, "out": out_path, **kwargs})
        if fail_at.index == len(calls):
            raise render.RenderError("encoder failed")
        Path(out_path).write_bytes(b"rendered clip")
        kwargs["render_info"].update(layout=kwargs.get("layout") or "blur", auto_facecam=False)
        return out_path

    monkeypatch.setattr(ingest, "resolve", resolve)
    monkeypatch.setattr(render, "render_clip", render_clip)
    monkeypatch.setattr(render, "probe", lambda path: {"width": 1080, "height": 1920, "has_audio": 1})
    monkeypatch.setattr(pipeline, "_words_for", lambda *a, **k: pytest.fail("should not transcribe"))
    return SimpleNamespace(
        root=tmp_path, media=media, source=source, calls=calls,
        ingests=ingests, statuses=statuses, fail_at=fail_at,
    )


def run_full(video, **kwargs):
    return clips.full(str(video.media), subtitles=False, on_status=video.statuses.append, **kwargs)


def add_existing(video, start, end, **kwargs):
    path = Path(clips_config.clips_dir()) / f"existing-{store.new_clip_id()}.mp4"
    path.write_bytes(b"existing")
    return store.add({
        "source_id": video.source.id, "path": str(path), "start": start, "end": end,
        "title": "Existing clip", **kwargs,
    })


def test_full_video_covers_first_second_to_short_final_remainder(full_video):
    result = run_full(full_video, clip_seconds=60)
    assert [(row["start"], row["end"]) for row in result["clips"]] == [(0, 60), (60, 120), (120, 125)]
    assert result["sequence"]["complete"] is True
    assert result["sequence"]["total_parts"] == 3
    assert result["sequence"]["remaining_parts"] == 0
    for index, row in enumerate(result["clips"], 1):
        assert row["sequence_index"] == index
        assert row["sequence_total"] == 3
        assert row["picked_by"] == "full"
        assert row["status"] == "draft"
        assert row["title"].endswith(f"Parte {index}")
        assert row["source_path"] == str(full_video.media)
        assert row["source_duration_seconds"] == 125
        assert "youtube_upload" not in row
    assert full_video.media.read_bytes() == b"source media"
    assert result["words_from"] == "none"


def test_default_batch_has_no_three_or_ten_part_limit(full_video):
    full_video.source.duration_seconds = 725
    result = run_full(full_video)
    assert len(result["clips"]) == 13
    assert result["clips"][-1]["end"] == 725


def test_batches_resume_missing_parts_without_repeating_created_parts(full_video):
    first = run_full(full_video, batch_size=1)
    second = run_full(full_video, batch_size=1)
    third = run_full(full_video)
    assert [row["sequence_index"] for row in first["clips"]] == [1]
    assert [row["sequence_index"] for row in second["clips"]] == [2]
    assert [row["sequence_index"] for row in third["clips"]] == [3]
    assert len(store.load()["clips"]) == 3
    assert {result["sequence"]["sequence_id"] for result in (first, second, third)} == {first["sequence"]["sequence_id"]}
    complete = run_full(full_video)
    assert complete["clips"] == []
    assert len(full_video.calls) == 3


def test_furthest_existing_clip_does_not_hide_earlier_holes(full_video):
    add_existing(full_video, 0, 60)
    add_existing(full_video, 120, 125)
    result = run_full(full_video)
    assert [(row["start"], row["end"]) for row in result["clips"]] == [(60, 120)]
    assert result["clips"][0]["sequence_index"] == 2
    assert result["sequence"]["complete"] is True


def test_missing_or_rejected_previous_part_can_be_created_again(full_video):
    result = run_full(full_video)
    Path(result["clips"][0]["path"]).unlink()
    store.set_status(result["clips"][1]["id"], "rejected")
    again = run_full(full_video)
    assert [row["sequence_index"] for row in again["clips"]] == [1, 2]


def test_resume_false_creates_a_new_render_without_deleting_old_parts(full_video):
    first = run_full(full_video)
    again = run_full(full_video, resume=False)
    assert len(again["clips"]) == 3
    assert len(store.load()["clips"]) == 6
    assert all(Path(row["path"]).is_file() for row in first["clips"])
    assert first["sequence"]["sequence_id"] != again["sequence"]["sequence_id"]


def test_new_limited_series_resumes_its_own_parts_after_an_older_complete_series(full_video):
    old = run_full(full_video)
    new = run_full(full_video, resume=False, batch_size=1)
    assert new["sequence"]["completed_parts"] == 1
    assert new["sequence"]["remaining_parts"] == 2
    assert new["sequence"]["complete"] is False
    assert new["sequence"]["sequence_id"] != old["sequence"]["sequence_id"]
    continued = run_full(full_video)
    assert [row["sequence_index"] for row in continued["clips"]] == [2, 3]
    assert continued["sequence"]["sequence_id"] == new["sequence"]["sequence_id"]
    assert continued["sequence"]["complete"] is True
    assert all(Path(row["path"]).is_file() for row in old["clips"])
    assert len(store.load()["clips"]) == 6


def test_failed_part_stops_batch_and_later_call_resumes_at_that_part(full_video):
    full_video.fail_at.index = 2
    first = run_full(full_video)
    assert [row["sequence_index"] for row in first["clips"]] == [1]
    assert first["failures"] and first["sequence"]["next_start"] == 60
    assert len(full_video.calls) == 2
    full_video.fail_at.index = None
    second = run_full(full_video)
    assert [row["sequence_index"] for row in second["clips"]] == [2, 3]


def test_first_part_failure_is_reported_without_skipping_to_later_parts(full_video):
    full_video.fail_at.index = 1
    with pytest.raises(pipeline.ClipError, match="siguiente parte"):
        run_full(full_video)
    assert len(full_video.calls) == 1
    assert store.load()["clips"] == []


def test_saved_boundaries_survive_subtitle_setting_changes_between_batches(full_video, monkeypatch):
    def transcript(*args, **kwargs):
        return {"source": "captions:file", "words": [
            {"start": 58, "end": 59, "word": "Termina."},
            {"start": 118, "end": 119, "word": "Otra."},
        ]}

    monkeypatch.setattr(pipeline, "_words_for", transcript)
    first = clips.full(str(full_video.media), batch_size=1, subtitles=True)
    assert first["clips"][0]["end"] == 59
    monkeypatch.setattr(pipeline, "_words_for", lambda *a, **k: pytest.fail("no subtitles requested"))
    second = run_full(full_video, batch_size=1)
    assert second["clips"][0]["start"] == 59
    assert second["clips"][0]["sequence_id"] == first["clips"][0]["sequence_id"]
    assert full_video.calls[0]["words"][0].start == 58
    assert full_video.calls[0]["duration"] == 59


def test_complete_sequence_does_not_transcribe_again(full_video):
    run_full(full_video)
    result = clips.full(str(full_video.media), subtitles=True)
    assert result["clips"] == []
    assert result["sequence"]["complete"]


def test_source_identity_change_invalidates_previous_render_coverage(full_video):
    first = run_full(full_video)
    full_video.media.write_bytes(b"a different version of this source video")
    second = run_full(full_video)
    assert len(second["clips"]) == 3
    assert second["sequence"]["sequence_id"] != first["sequence"]["sequence_id"]


def test_plan_and_local_source_manifest_stay_in_module_work_dir(full_video):
    result = run_full(full_video, batch_size=1)
    directory = Path(clips_config.source_dir(full_video.source.id))
    plan = json.loads((directory / "sequence-60.json").read_text(encoding="utf-8"))
    assert plan["source_id"] == full_video.source.id
    assert plan["spans"] == [[0, 60], [60, 120], [120, 125]]
    assert plan["sequence_id"] == result["sequence"]["sequence_id"]
    assert (directory / "source.json").is_file()
    assert not (full_video.media.parent / "source.json").exists()


def test_one_source_cannot_run_two_continuations_at_the_same_time(full_video):
    token = hashlib.sha256(full_video.source.id.encode("utf-8")).hexdigest()[:24]
    path = Path(clips_config.clips_dir()) / f".full-sequence-{token}.lock"
    with store.file_lock(str(path), timeout=0):
        with pytest.raises(pipeline.ClipError, match="continuación en curso"):
            run_full(full_video)
    assert full_video.calls == []


def test_busy_target_is_refused_before_ingest_can_replace_the_source(full_video, monkeypatch):
    monkeypatch.setattr(
        ingest, "resolve", lambda *args, **kwargs: pytest.fail("must lock before ingestion")
    )
    with store.file_lock(pipeline._full_target_lock_path(str(full_video.media)), timeout=0):
        with pytest.raises(pipeline.ClipError, match="continuación en curso"):
            run_full(full_video, refresh=True)
    assert full_video.calls == []


def test_equivalent_youtube_urls_share_the_pre_ingest_lock(full_video, monkeypatch):
    share = "https://youtu.be/abcdefghijk?t=10"
    watch = "https://www.youtube.com/watch?v=abcdefghijk"
    assert pipeline._full_target_lock_path(share) == pipeline._full_target_lock_path(watch)
    monkeypatch.setattr(
        ingest, "resolve", lambda *args, **kwargs: pytest.fail("must not refresh a source in use")
    )
    with store.file_lock(pipeline._full_target_lock_path(share), timeout=0):
        with pytest.raises(pipeline.ClipError, match="continuación en curso"):
            clips.full(watch, rights_note="mi canal", subtitles=False, refresh=True)


def test_local_path_aliases_share_the_pre_ingest_lock(full_video):
    alias = full_video.media.parent / "folder" / ".." / full_video.media.name
    assert pipeline._full_target_lock_path(str(alias)) == pipeline._full_target_lock_path(str(full_video.media))


def test_live_snapshot_uses_materialized_relative_offsets(full_video, monkeypatch):
    full_video.source.live = True
    full_video.source.duration_seconds = 65
    spans = []

    def materialize(source, start, end, on_status):
        spans.append((start, end))
        return "downloaded-piece.mp4", max(0, start - 2)

    monkeypatch.setattr(ingest, "materialize_span", materialize)
    result = run_full(full_video)
    assert spans == [(0, 60), (60, 65)]
    assert [(call["source"], call["start"]) for call in full_video.calls] == [
        ("downloaded-piece.mp4", 0), ("downloaded-piece.mp4", 2),
    ]
    assert result["source"]["live"] is True
    assert any("saved snapshot only" in status for status in full_video.statuses)


def test_no_speech_keeps_the_entire_video_even_when_subtitles_were_enabled(full_video, monkeypatch):
    def no_words(*args, **kwargs):
        raise transcribe.TranscribeError("Whisper produced no words. If the source has no speech there is nothing to clip on.")

    monkeypatch.setattr(pipeline, "_words_for", no_words)
    result = clips.full(str(full_video.media), subtitles=True)
    assert len(result["clips"]) == 3
    assert result["words_from"] == "none"


def test_real_transcription_errors_are_not_silently_treated_as_silence(full_video, monkeypatch):
    def failed(*args, **kwargs):
        raise transcribe.TranscribeError("Whisper failed: unavailable model")

    monkeypatch.setattr(pipeline, "_words_for", failed)
    with pytest.raises(transcribe.TranscribeError, match="unavailable model"):
        clips.full(str(full_video.media), subtitles=True)
    assert full_video.calls == []


@pytest.mark.parametrize("duration", [-1, math.nan, math.inf])
def test_invalid_source_duration_is_rejected_before_rendering(full_video, duration):
    full_video.source.duration_seconds = duration
    with pytest.raises(pipeline.ClipError, match="duración"):
        run_full(full_video)
    assert full_video.calls == []


@pytest.mark.parametrize("kwargs", [
    {"clip_seconds": 0}, {"clip_seconds": math.nan}, {"clip_seconds": 181},
    {"batch_size": -1}, {"batch_size": 1.5},
])
def test_invalid_full_video_parameters_do_not_ingest(full_video, kwargs):
    with pytest.raises(pipeline.ClipError):
        run_full(full_video, **kwargs)
    assert full_video.ingests == []


def test_url_rights_and_download_limits_follow_the_existing_ingest_flow(full_video):
    with pytest.raises(pipeline.ClipError, match="rights"):
        clips.full("https://example.com/video", subtitles=False)
    result = clips.full(
        "https://example.com/video", subtitles=False, rights_note="mi canal",
        language="es", max_minutes=240, layout="crop", facecam="", title_overlay="never",
    )
    assert full_video.ingests[0][1:] == ("mi canal", {
        "refresh": False, "max_minutes": 240, "language": "es",
        "on_status": full_video.ingests[0][2]["on_status"],
    })
    assert all(call["layout"] == "crop" and call["title_overlay"] == "never" for call in full_video.calls)
    assert result["sequence"]["complete"]
