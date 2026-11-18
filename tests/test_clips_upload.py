"""Exact clip uploads, saved Firefox profiles and durable upload outcomes."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from clips import config as clips_config  # noqa: E402
from clips import store, upload  # noqa: E402


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(clips_config, "root_dir", lambda: str(tmp_path))
    profile = tmp_path / "profile-channel"
    profile.mkdir()
    fallback = tmp_path / "profile-default"
    fallback.mkdir()
    (tmp_path / "config.json").write_text(
        json.dumps({"firefox_profile": str(fallback)}), encoding="utf-8"
    )
    (tmp_path / ".mp").mkdir()
    channel = {
        "id": "channel-1", "nickname": "Mi canal", "firefox_profile": str(profile),
        "niche": "Videojuegos", "language": "español",
    }
    cache = tmp_path / ".mp" / "youtube.json"
    cache.write_text(json.dumps({"accounts": [channel]}), encoding="utf-8")
    media = Path(clips_config.clips_dir()) / "clip-1.mp4"
    media.write_bytes(b"test rendered video")
    subs = media.with_suffix(".ass")
    subs.write_text("test subtitles", encoding="utf-8")
    clip = store.add({
        "id": "clip-1", "title": "Mi mejor jugada", "path": str(media),
        "status": "approved", "transcript": "Una jugada increíble.",
        "source_title": "Partida original", "source_url": "https://youtu.be/abcdefghijk",
    })
    return SimpleNamespace(
        root=tmp_path, profile=profile, fallback=fallback, channel=channel,
        cache=cache, media=media, subs=subs, clip=clip,
    )


def fake_youtube(monkeypatch, *, result=True, url="https://www.youtube.com/shorts/abcdefghijk", confirmed=True, error=None, during=None):
    created = []

    class YouTube:
        def __init__(self, *args):
            self.args = args
            self.uploaded_video_url = url
            self.upload_confirmed = confirmed
            created.append(self)

        def upload_video(self):
            if during:
                during(self)
            if error:
                raise error
            return result

    monkeypatch.setattr(upload, "_youtube_class", lambda: YouTube)
    return created


def test_validation_uses_channel_profile_without_constructing_a_browser(workspace, monkeypatch):
    monkeypatch.setattr(upload, "_youtube_class", lambda: pytest.fail("validation must not create Firefox"))
    prepared = upload.validate_upload("clip-1", "channel-1")
    assert prepared["path"] == str(workspace.media.resolve())
    assert prepared["profile"] == str(workspace.profile.resolve())
    assert prepared["title"] == "Mi mejor jugada"
    assert "Partida original" in prepared["description"]
    assert "https://youtu.be/abcdefghijk" in prepared["description"]
    assert "#Shorts" in prepared["description"]
    assert "youtube_upload" not in store.load()["clips"][0]


def test_empty_channel_profile_falls_back_to_config(workspace):
    workspace.channel["firefox_profile"] = "  "
    workspace.cache.write_text(json.dumps({"accounts": [workspace.channel]}), encoding="utf-8")
    assert upload.validate_upload("clip-1", "channel-1")["profile"] == str(workspace.fallback.resolve())


def test_invalid_channel_profile_does_not_silently_switch_accounts(workspace):
    workspace.channel["firefox_profile"] = str(workspace.root / "not-a-profile")
    workspace.cache.write_text(json.dumps({"accounts": [workspace.channel]}), encoding="utf-8")
    with pytest.raises(upload.UploadError, match="perfil de Firefox"):
        upload.validate_upload("clip-1", "channel-1")


def test_exact_clip_and_metadata_use_the_existing_short_uploader(workspace, monkeypatch):
    created = fake_youtube(monkeypatch)
    updated = upload.upload_clip("clip-1", "channel-1", title="Mi título", description="Mi descripción")
    youtube = created[0]
    assert youtube.args == ("channel-1", "Mi canal", str(workspace.profile.resolve()), "Videojuegos", "español")
    assert youtube.video_path == str(workspace.media.resolve())
    assert youtube.subject == "Mi título"
    assert youtube.metadata == {"title": "Mi título", "description": "Mi descripción"}
    assert youtube._is_long_video is False
    assert youtube._defer_upload_cleanup is True
    assert updated["status"] == "published"
    assert updated["youtube_upload"]["status"] == "uploaded"
    assert updated["youtube_upload"]["url"] == youtube.uploaded_video_url
    assert updated["youtube_upload"]["started_at"]
    assert updated["youtube_upload"]["finished_at"]
    assert workspace.media.is_file() and workspace.subs.is_file()


@pytest.mark.parametrize("result,error", [(False, None), (True, RuntimeError("Firefox error"))])
def test_failure_keeps_media_and_allows_a_later_retry(workspace, monkeypatch, result, error):
    fake_youtube(monkeypatch, result=result, error=error)
    with pytest.raises(upload.UploadError):
        upload.upload_clip("clip-1", "channel-1")
    clip = store.load()["clips"][0]
    assert clip["status"] == "approved"
    assert clip["youtube_upload"]["status"] == "failed"
    assert clip["youtube_upload"]["error"]
    assert workspace.media.is_file() and workspace.subs.is_file()
    fake_youtube(monkeypatch)
    assert upload.upload_clip("clip-1", "channel-1")["status"] == "published"


@pytest.mark.parametrize("url", ["", "https://studio.youtube.com", "https://example.com/watch?v=abcdefghijk"])
def test_unconfirmed_upload_is_not_marked_published_or_retried(workspace, monkeypatch, url):
    created = fake_youtube(monkeypatch, url=url)
    updated = upload.upload_clip("clip-1", "channel-1")
    assert updated["status"] == "approved"
    assert updated["youtube_upload"]["status"] == "pending_confirmation"
    assert updated["youtube_upload"]["url"] == ""
    with pytest.raises(upload.UploadError, match="verificación"):
        upload.upload_clip("clip-1", "channel-1")
    assert len(created) == 1


def test_video_url_without_finished_upload_stays_pending_and_blocks_retry(workspace, monkeypatch):
    url = "https://www.youtube.com/shorts/abcdefghijk"
    created = fake_youtube(monkeypatch, url=url, confirmed=False)
    updated = upload.upload_clip("clip-1", "channel-1")
    assert updated["status"] == "approved"
    assert updated["youtube_upload"]["status"] == "pending_confirmation"
    assert updated["youtube_upload"]["url"] == url
    with pytest.raises(upload.UploadError, match="verificación"):
        upload.upload_clip("clip-1", "channel-1")
    assert len(created) == 1
    assert workspace.media.is_file() and workspace.subs.is_file()


def test_a_second_upload_of_a_published_clip_never_opens_firefox(workspace, monkeypatch):
    created = fake_youtube(monkeypatch)
    upload.upload_clip("clip-1", "channel-1")
    with pytest.raises(upload.UploadError, match="ya se subió"):
        upload.upload_clip("clip-1", "channel-1")
    assert len(created) == 1


@pytest.mark.parametrize("status", ["rejected", "published"])
def test_disallowed_review_status_is_rejected_before_browser_work(workspace, monkeypatch, status):
    store.set_status("clip-1", status)
    monkeypatch.setattr(upload, "_youtube_class", lambda: pytest.fail("must not create Firefox"))
    with pytest.raises(upload.UploadError):
        upload.upload_clip("clip-1", "channel-1")


def test_missing_file_channel_profile_and_clip_are_actionable(workspace):
    with pytest.raises(upload.UploadError, match="clip no existe"):
        upload.validate_upload("missing", "channel-1")
    with pytest.raises(upload.UploadError, match="canal.*no existe"):
        upload.validate_upload("clip-1", "missing")
    workspace.media.unlink()
    with pytest.raises(upload.UploadError, match="archivo.*no existe"):
        upload.validate_upload("clip-1", "channel-1")
    workspace.media.write_bytes(b"again")
    workspace.channel["firefox_profile"] = ""
    workspace.cache.write_text(json.dumps({"accounts": [workspace.channel]}), encoding="utf-8")
    (workspace.root / "config.json").write_text("{}", encoding="utf-8")
    with pytest.raises(upload.UploadError, match="Configura un perfil"):
        upload.validate_upload("clip-1", "channel-1")


def test_catalogue_cannot_upload_an_unrelated_local_file(workspace):
    outside = workspace.root / "other.mp4"
    outside.write_bytes(b"not a rendered clip")
    store.update("clip-1", {"path": str(outside)})
    with pytest.raises(upload.UploadError, match="carpeta de clips"):
        upload.validate_upload("clip-1", "channel-1")


def test_edits_and_defaults_respect_metadata_limits(workspace):
    store.update("clip-1", {"title": "x" * 150, "transcript": "palabra " * 1000})
    prepared = upload.validate_upload("clip-1", "channel-1")
    assert len(prepared["title"]) == 100
    assert len(prepared["description"]) <= 5000
    assert prepared["description"].endswith("#Shorts")
    for metadata in ({"title": " "}, {"description": " "}, {"title": "x" * 101}, {"description": "x" * 5001}):
        with pytest.raises(upload.UploadError):
            upload.validate_upload("clip-1", "channel-1", **metadata)


def test_other_clips_added_during_upload_and_review_updates_are_preserved(workspace, monkeypatch):
    def during(youtube):
        store.add({"id": "clip-2", "title": "Otro clip", "path": str(workspace.media)})
        store.update("clip-1", {"review_note": "Revisado mientras subía"})

    fake_youtube(monkeypatch, during=during)
    upload.upload_clip("clip-1", "channel-1")
    rows = {clip["id"]: clip for clip in store.load()["clips"]}
    assert rows["clip-1"]["review_note"] == "Revisado mientras subía"
    assert rows["clip-1"]["status"] == "published"
    assert rows["clip-2"]["title"] == "Otro clip"
    store.remove("clip-2")
    assert store.load()["clips"][0]["youtube_upload"]["status"] == "uploaded"


def test_global_upload_lock_prevents_a_second_clip_interrupting_firefox(workspace, monkeypatch):
    monkeypatch.setattr(upload, "_youtube_class", lambda: pytest.fail("must not create Firefox"))
    path = str(Path(clips_config.clips_dir()) / ".youtube-upload.lock")
    with store.file_lock(path, timeout=0):
        with pytest.raises(upload.UploadError, match="subida de clips en curso"):
            upload.upload_clip("clip-1", "channel-1")
    assert "youtube_upload" not in store.load()["clips"][0]


def test_interrupted_in_progress_status_requires_studio_review_before_reopening(workspace, monkeypatch):
    store.update("clip-1", {"youtube_upload": {"status": "uploading"}})
    monkeypatch.setattr(upload, "_youtube_class", lambda: pytest.fail("must not create Firefox"))
    with pytest.raises(upload.UploadError, match="verificación"):
        upload.upload_clip("clip-1", "channel-1")
    assert store.load()["clips"][0]["youtube_upload"]["status"] == "pending_confirmation"


def test_reconcile_orphan_preserves_metadata_and_never_marks_it_failed(workspace):
    previous = {
        "status": "uploading", "channel_id": "channel-1", "title": "Título confirmado",
        "description": "Descripción", "url": "https://youtu.be/abcdefghijk",
        "started_at": "2026-11-14T12:00:00+00:00", "upload_confirmed": False,
    }
    store.update("clip-1", {"youtube_upload": previous})
    upload.reconcile_uploads()
    record = store.load()["clips"][0]
    state = record["youtube_upload"]
    assert state["status"] == "pending_confirmation"
    assert record["status"] == "approved"
    assert "interrumpió" in state["error"]
    for field in ("channel_id", "title", "description", "url", "started_at", "upload_confirmed"):
        assert state[field] == previous[field]


def test_reconcile_leaves_a_live_worker_unchanged(workspace):
    previous = {"status": "uploading", "channel_id": "channel-1"}
    store.update("clip-1", {"youtube_upload": previous})
    with store.file_lock(upload._global_lock_path(), timeout=0):
        upload.reconcile_uploads()
        assert store.load()["clips"][0]["youtube_upload"] == previous


@pytest.mark.parametrize("operation", ["validate", "upload"])
def test_pending_other_clip_blocks_every_new_upload(workspace, monkeypatch, operation):
    store.add({"id": "clip-pending", "title": "Pendiente", "path": str(workspace.media),
               "youtube_upload": {"status": "pending_confirmation"}})
    monkeypatch.setattr(upload, "_youtube_class", lambda: pytest.fail("must not create Firefox"))
    callback = upload.validate_upload if operation == "validate" else upload.upload_clip
    with pytest.raises(upload.UploadError, match="pendiente de confirmación"):
        callback("clip-1", "channel-1")
    assert "youtube_upload" not in store.load()["clips"][0]


def test_validation_reconciles_an_orphan_in_a_different_clip(workspace):
    store.add({"id": "clip-orphan", "youtube_upload": {"status": "uploading"}})
    with pytest.raises(upload.UploadError, match="pendiente de confirmación"):
        upload.validate_upload("clip-1", "channel-1")
    assert store.load()["clips"][1]["youtube_upload"]["status"] == "pending_confirmation"


def test_manually_confirming_pending_upload_marks_published_and_keeps_media(workspace, monkeypatch):
    previous = {
        "status": "pending_confirmation", "channel_id": "channel-1", "title": "Mi título",
        "url": "", "started_at": "original-time", "error": "interrumpido",
    }
    store.update("clip-1", {"youtube_upload": previous, "review_note": "Conservar"})
    monkeypatch.setattr(upload, "_youtube_class", lambda: pytest.fail("resolution must not open Firefox"))
    result = upload.resolve_upload("clip-1", "published", "https://youtu.be/abcdefghijk")
    assert result["status"] == "published"
    assert result["youtube_upload"]["status"] == "uploaded"
    assert result["youtube_upload"]["confirmed_manually"] is True
    assert result["youtube_upload"]["url"] == "https://youtu.be/abcdefghijk"
    assert result["youtube_upload"]["error"] == ""
    assert result["youtube_upload"]["started_at"] == "original-time"
    assert result["review_note"] == "Conservar"
    assert workspace.media.is_file() and workspace.subs.is_file()


@pytest.mark.parametrize("url", ["", "https://studio.youtube.com", "https://example.com/watch?v=abcdefghijk"])
def test_manual_publication_requires_a_real_video_url(workspace, url):
    store.update("clip-1", {"youtube_upload": {"status": "pending_confirmation"}})
    with pytest.raises(upload.UploadError, match="URL válida"):
        upload.resolve_upload("clip-1", "published", url)
    assert store.load()["clips"][0]["youtube_upload"]["status"] == "pending_confirmation"


def test_explicit_retry_resolution_only_unblocks_a_later_upload(workspace, monkeypatch):
    store.update("clip-1", {"youtube_upload": {
        "status": "pending_confirmation", "title": "Conservar título", "error": "Revisar",
    }})
    created = fake_youtube(monkeypatch)
    result = upload.resolve_upload("clip-1", "retry")
    assert created == []
    assert result["status"] == "approved"
    assert result["youtube_upload"]["status"] == "failed"
    assert result["youtube_upload"]["error"] == ""
    assert result["youtube_upload"]["title"] == "Conservar título"
    assert upload.validate_upload("clip-1", "channel-1")
    assert upload.upload_clip("clip-1", "channel-1")["status"] == "published"
    assert len(created) == 1
    assert workspace.media.is_file() and workspace.subs.is_file()


@pytest.mark.parametrize("status", [None, "uploaded", "failed"])
def test_resolution_does_not_change_nonpending_records(workspace, status):
    if status:
        store.update("clip-1", {"youtube_upload": {"status": status}})
    before = store.load()["clips"][0]
    with pytest.raises(upload.UploadError, match="pendiente de confirmación"):
        upload.resolve_upload("clip-1", "retry")
    assert store.load()["clips"][0] == before


def test_resolution_cannot_interrupt_a_live_worker(workspace):
    state = {"status": "uploading", "channel_id": "channel-1"}
    store.update("clip-1", {"youtube_upload": state})
    with store.file_lock(upload._global_lock_path(), timeout=0):
        with pytest.raises(upload.UploadError, match="subida de clips en curso"):
            upload.resolve_upload("clip-1", "retry")
    assert store.load()["clips"][0]["youtube_upload"] == state


def test_resolution_can_confirm_a_reconciled_orphan_and_rejects_unknown_actions(workspace):
    store.update("clip-1", {"youtube_upload": {"status": "uploading"}})
    with pytest.raises(upload.UploadError, match="Elige confirmar"):
        upload.resolve_upload("clip-1", "unknown")
    result = upload.resolve_upload("clip-1", "published", "https://youtu.be/abcdefghijk")
    assert result["status"] == "published"


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=abcdefghijk", "https://youtu.be/abcdefghijk",
    "https://youtube.com/shorts/abcdefghijk",
])
def test_video_urls_are_accepted_without_treating_studio_as_confirmation(url):
    assert upload._video_url(url) == url
    assert upload._video_url("https://studio.youtube.com/video/abcdefghijk/edit") == ""
