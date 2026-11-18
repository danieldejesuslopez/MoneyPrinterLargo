"""Narrated batches: provenance, channel binding, resume, and upload boundaries."""

import importlib
import json
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "webapp" / "api"))

from gta6 import Workspace, channels, editorial as ed, graphics, news
from clips import config as cc, store as clip_store, upload
import gta6_api


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "root_dir", lambda: str(tmp_path))
    channel = channels.register("Porque GTA", "@PorqueGTA", youtube_channel_id="UC" + "a" * 22, root=tmp_path)
    original = channels.for_generation
    monkeypatch.setattr(channels, "for_generation", lambda cid, auto: original(cid, auto, root=tmp_path))
    w = Workspace(tmp_path / "editorial")
    facts = [w.fact({"text": f"Este es el hecho oficial número {i + 1}.", "url": "https://www.rockstargames.com/VI", "status": "verified", "reviewed": True}) for i in range(4)]
    w.settings({"youtube_channel_id": channel["id"]})
    return w, channel, facts, tmp_path


@pytest.fixture
def fake_render(monkeypatch, setup):
    calls = []
    def render(workspace, job, item, on_status):
        calls.append(item["key"])
        path = Path(cc.clips_dir()) / f"{item['key']}.mp4"
        path.write_bytes(b"fixture-video")
        info = news.story(item["fact"], job["settings"])
        return clip_store.add({"id": item["key"], "path": str(path), "duration": 20.0,
                               "title": info["title"], "gta6_news_key": item["key"]})
    monkeypatch.setattr(news, "render_story", render)
    return calls


def test_register_is_idempotent_and_preserves_other_channels(tmp_path):
    target = tmp_path / ".mp" / "youtube.json"
    target.parent.mkdir()
    target.write_text(json.dumps({"accounts": [{"id": "old", "nickname": "Savirox"}], "extra": "preserved"}))
    first = channels.register("Porque GTA", "https://www.youtube.com/@PorqueGTA", root=tmp_path)
    second = channels.register("Porque GTA", "@PorqueGTA", root=tmp_path)
    assert first["id"] == second["id"]
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["extra"] == "preserved"
    assert data["accounts"][0]["nickname"] == "Savirox"
    assert len(data["accounts"]) == 2
    assert first["automation_profile_verified"] is False


def test_unidentified_firefox_channel_is_explicitly_pending(tmp_path):
    row = channels.register("Firefox · por identificar", pending_firefox=True, root=tmp_path)
    assert row["gta6_pending_identity"]
    assert not row["youtube_handle"]
    with pytest.raises(ValueError, match="identificado"):
        channels.for_generation(row["id"], False, root=tmp_path)


def test_profile_requires_identity_confirmation(setup):
    w, channel, facts, root = setup
    path = root / "profile"
    path.mkdir()
    with pytest.raises(ValueError, match="confirma"):
        channels.bind(channel["id"], profile=str(path), confirmed=False, root=root)
    bound = channels.bind(channel["id"], profile=str(path), confirmed=True, root=root)
    assert bound["automation_profile_verified"]
    assert channels.for_generation(channel["id"], True)["firefox_profile"] == str(path.resolve())


def test_unbound_channel_can_generate_but_cannot_auto_upload(setup):
    w, channel, facts, root = setup
    with pytest.raises(ValueError, match="perfil"):
        news.request(w, {"count": 2, "auto_upload": True})
    assert news.request(w, {"count": 2, "auto_upload": False})["status"] == "queued"
    with pytest.raises(upload.UploadError, match="perfil"):
        upload.resolve_profile(channel)


@pytest.mark.parametrize("value", ["https://evil.test/@name", "bad handle", "@x", "https://youtube.com/watch?v=123"])
def test_channel_handle_validation(value):
    with pytest.raises(ValueError):
        channels.normalize_handle(value)


def test_profile_list_reads_only_names_and_paths(tmp_path, monkeypatch):
    base = tmp_path / "Mozilla" / "Firefox"
    base.mkdir(parents=True)
    (base / "profiles.ini").write_text("[Profile0]\nName=work\nIsRelative=1\nPath=Profiles/work\n")
    (base / "Profiles" / "work").mkdir(parents=True)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    rows = channels.profiles()
    assert rows == [{"name": "work", "path": str((base / "Profiles" / "work").resolve()), "exists": True}]


def test_news_rejects_stale_facts_before_a_job_is_created(setup):
    w, channel, facts, root = setup
    with w.store.edit() as state:
        for f in state["facts"]:
            f["verified_at"] = (ed.now() - timedelta(hours=49)).isoformat()
    with pytest.raises(ValueError, match="vigentes"):
        news.request(w, {"count": 2})
    assert not w.store.read()["news_jobs"]


def test_story_preserves_verified_sentence_verbatim(setup):
    w, channel, facts, root = setup
    info = news.story(facts[0], {"language": "español", "name": "Porque GTA"})
    assert facts[0]["text"] in info["script"]
    assert facts[0]["url"] in info["description"]
    assert "no es gameplay" in info["description"]


def test_original_brand_assets_have_expected_dimensions(tmp_path):
    paths = graphics.brand(tmp_path, "Porque GTA", "@PorqueGTA")
    with Image.open(paths["avatar"]) as image:
        assert image.size == (800, 800)
    with Image.open(paths["banner"]) as image:
        assert image.size == (2560, 1440)


def test_generation_does_not_open_an_uploader_and_resume_is_idempotent(setup, fake_render, monkeypatch):
    w, channel, facts, root = setup
    monkeypatch.setattr(upload, "upload_clip", lambda *a, **k: pytest.fail("rendering drafts must not upload"))
    job = news.request(w, {"count": 2, "text_posts": 3})
    result = news.run(w, job["id"], on_status=lambda m: None)
    assert result["status"] == "done"
    assert len(result["text_post_ids"]) == 3
    assert len(fake_render) == 2
    assert len(w.store.read()["posts"]) == 9  # YouTube + Reels + TikTok, plus X texts.
    news.run(w, job["id"], on_status=lambda m: None)
    assert len(fake_render) == 2
    assert all(p["status"] == "draft" for p in w.store.read()["posts"])


def test_queued_and_failed_batches_reserve_their_facts(setup):
    w, channel, facts, root = setup
    one = news.request(w, {"count": 2, "fact_ids": [f["id"] for f in facts[:2]]})
    with w.store.edit() as state:
        state["news_jobs"][0]["status"] = "error"
    with pytest.raises(ValueError, match="otro lote"):
        news.request(w, {"count": 2, "fact_ids": [f["id"] for f in facts[:2]]})
    assert news.request(w, {"count": 2, "fact_ids": [f["id"] for f in facts[:2]], "allow_reuse": True})


def test_resume_keeps_the_first_video_after_a_second_render_fails(setup, fake_render, monkeypatch):
    w, channel, facts, root = setup
    base = news.render_story
    def fail_second(workspace, job, item, on_status):
        if len(fake_render) == 1:
            raise RuntimeError("encoder stopped")
        return base(workspace, job, item, on_status)
    monkeypatch.setattr(news, "render_story", fail_second)
    job = news.request(w, {"count": 2})
    with pytest.raises(RuntimeError):
        news.run(w, job["id"], on_status=lambda m: None)
    assert w.store.read()["news_jobs"][0]["status"] == "error"
    monkeypatch.setattr(news, "render_story", base)
    assert news.run(w, job["id"], on_status=lambda m: None)["status"] == "done"
    assert len(fake_render) == 2


def test_fact_change_after_queueing_stops_generation(setup, fake_render):
    w, channel, facts, root = setup
    job = news.request(w, {"count": 1})
    w.fact({"text": "El hecho cambió", "url": facts[0]["url"], "status": "verified", "reviewed": True}, facts[0]["id"])
    with pytest.raises(ValueError, match="cambió"):
        news.run(w, job["id"], on_status=lambda m: None)
    assert not fake_render


def test_automatic_upload_only_runs_after_the_explicit_batch_option(setup, fake_render, monkeypatch):
    w, channel, facts, root = setup
    profile = root / "profile"
    profile.mkdir()
    channels.bind(channel["id"], profile=str(profile), confirmed=True, root=root)
    calls = []
    def fake_upload(cid, channel_id, **kwargs):
        calls.append(cid)
        return clip_store.update(cid, {"youtube_upload": {"status": "uploaded", "url": "https://www.youtube.com/shorts/abcdefghijk"}})
    monkeypatch.setattr(upload, "upload_clip", fake_upload)
    job = news.request(w, {"count": 2, "auto_upload": True})
    assert not calls
    result = news.run(w, job["id"], on_status=lambda m: None)
    assert len(calls) == 2
    assert all(item["status"] == "uploaded" for item in result["items"])
    assert sum(p["status"] == "published" for p in w.store.read()["posts"]) == 2


def test_pending_upload_stops_the_batch_and_does_not_retry(setup, fake_render, monkeypatch):
    w, channel, facts, root = setup
    profile = root / "profile"
    profile.mkdir()
    channels.bind(channel["id"], profile=str(profile), confirmed=True, root=root)
    calls = []
    def fake_upload(cid, channel_id, **kwargs):
        calls.append(cid)
        return clip_store.update(cid, {"youtube_upload": {"status": "pending_confirmation"}})
    monkeypatch.setattr(upload, "upload_clip", fake_upload)
    job = news.request(w, {"count": 2, "auto_upload": True})
    with pytest.raises(ValueError, match="subida"):
        news.run(w, job["id"], on_status=lambda m: None)
    with pytest.raises(ValueError, match="pendiente"):
        news.run(w, job["id"], on_status=lambda m: None)
    assert len(calls) == 1
    assert len(fake_render) == 1


def test_editing_or_rechecking_clears_automatic_approval(setup):
    w, channel, facts, root = setup
    p = w.post({"title": "test", "text": "Texto", "platform": "x", "pillar": "news", "fact_ids": [facts[0]["id"]]})
    with w.store.edit() as state:
        state["posts"][0].update(automation_approval="verified-facts-original-cards", status="approved")
    w.fact({**facts[0], "reviewed": True}, facts[0]["id"])
    assert "automation_approval" not in w.store.read()["posts"][0]
    with w.store.edit() as state:
        state["posts"][0]["automation_approval"] = "verified-facts-original-cards"
    w.post({"text": "Cambió el texto"}, p["id"])
    assert "automation_approval" not in w.store.read()["posts"][0]


def test_news_api_starts_the_shared_job_runner(setup, monkeypatch):
    w, channel, facts, root = setup
    monkeypatch.setattr(gta6_api, "workspace", w)
    calls = []
    class Job:
        id = "newsjob"
    monkeypatch.setattr(gta6_api, "_spawn_job", lambda args, **kw: calls.append(args) or Job())
    app = FastAPI()
    app.include_router(gta6_api.router)
    client = TestClient(app)
    response = client.post("/api/gta6/news", json={"channel_id": channel["id"], "count": 2, "auto_upload": False})
    assert response.status_code == 200
    assert calls[0][:3] == ["gta6", "news", "--request-id"]
    assert response.json()["stream_url"] == "/api/jobs/newsjob/stream"


def test_unverified_channel_is_blocked_before_any_browser_is_opened(monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, "ROOT_DIR", str(ROOT))
    module = importlib.import_module("classes.YouTube")
    monkeypatch.setattr(module, "get_accounts", lambda provider: [{"id": "channel", "automation_profile_verified": False, "upload_disabled_reason": "profile not bound"}])
    obj = module.YouTube.__new__(module.YouTube)
    obj._account_uuid = "channel"
    obj.subject = "Subject"
    obj.video_path = str(tmp_path / "video.mp4")
    Path(obj.video_path).write_bytes(b"fixture")
    obj._ensure_browser = lambda: pytest.fail("No browser should be opened")
    assert obj.upload_video() is False


def test_resume_reconciles_a_confirmed_upload_without_sending_it_again(setup, fake_render, monkeypatch):
    w, channel, facts, root = setup
    profile = root / "profile"
    profile.mkdir()
    channels.bind(channel["id"], profile=str(profile), confirmed=True, root=root)
    calls = []
    def interrupted_after_remote_success(cid, channel_id, **kwargs):
        calls.append(cid)
        clip_store.update(cid, {"youtube_upload": {"status": "uploaded", "url": "https://www.youtube.com/shorts/abcdefghijk"}})
        raise RuntimeError("worker interrupted after upload")
    monkeypatch.setattr(upload, "upload_clip", interrupted_after_remote_success)
    job = news.request(w, {"count": 1, "auto_upload": True})
    with pytest.raises(RuntimeError):
        news.run(w, job["id"], on_status=lambda m: None)
    result = news.run(w, job["id"], on_status=lambda m: None)
    assert result["status"] == "done"
    assert result["items"][0]["status"] == "uploaded"
    assert len(calls) == 1


def test_uploader_refuses_a_different_signed_in_channel(monkeypatch, tmp_path, capsys):
    import config
    monkeypatch.setattr(config, "ROOT_DIR", str(ROOT))
    module = importlib.import_module("classes.YouTube")
    expected = "UC" + "a" * 22
    wrong = "UC" + "b" * 22
    monkeypatch.setattr(module, "get_accounts", lambda provider: [{"id": "selected", "gta6_managed": True,
                          "automation_profile_verified": True, "youtube_channel_id": expected}])
    monkeypatch.setattr(module, "get_verbose", lambda: False)
    obj = module.YouTube.__new__(module.YouTube)
    obj._account_uuid = "selected"
    obj.subject = "A fact"
    obj.video_path = str(tmp_path / "video.mp4")
    Path(obj.video_path).write_bytes(b"fixture")
    class Browser:
        def get(self, url):
            pytest.fail("Should stop before navigating to upload")
    obj.browser = Browser()
    obj._ensure_browser = lambda: None
    obj.get_channel_id = lambda: setattr(obj, "channel_id", wrong)
    assert obj.upload_video() is False
    assert "no corresponde" in capsys.readouterr().out


def test_changing_handle_in_channels_invalidates_the_saved_binding(monkeypatch):
    from webapp.api import main
    row = {"id": "selected", "nickname": "GTA", "gta6_managed": True, "automation_profile_verified": True,
           "youtube_handle": "@OldHandle", "youtube_channel_id": "UC" + "a" * 22, "firefox_profile": "profile"}
    data = {"accounts": [row]}
    monkeypatch.setattr(main, "_read_youtube_raw", lambda: data)
    writes = []
    monkeypatch.setattr(main, "_write_youtube_raw", writes.append)
    main.update_channel("selected", main.YouTubeChannelIn(nickname="GTA", youtube_handle="@NewHandle", firefox_profile="profile"))
    assert row["automation_profile_verified"] is False
    assert row["youtube_channel_id"] == ""
    assert len(writes) == 1


def test_explicit_publication_of_a_draft_batch_reuses_its_videos(setup, fake_render, monkeypatch):
    w, channel, facts, root = setup
    job = news.request(w, {"count": 2, "auto_upload": False})
    news.run(w, job["id"], on_status=lambda m: None)
    profile = root / "profile"
    profile.mkdir()
    channels.bind(channel["id"], profile=str(profile), confirmed=True, root=root)
    calls = []
    def fake_upload(cid, channel_id, **kwargs):
        calls.append(cid)
        return clip_store.update(cid, {"youtube_upload": {"status": "uploaded", "url": "https://www.youtube.com/shorts/abcdefghijk"}})
    monkeypatch.setattr(upload, "upload_clip", fake_upload)
    queued = news.publish_rendered(w, job["id"])
    assert queued["auto_upload"]
    assert len(calls) == 0
    done = news.run(w, job["id"], on_status=lambda m: None)
    assert len(calls) == 2
    assert len(fake_render) == 2
    assert done["status"] == "done"
    with pytest.raises(ValueError):
        news.publish_rendered(w, job["id"])
