"""Exercise real workspace transitions without contacting a social account."""
import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gta6 import Workspace
from gta6 import hermes_live as live, hermes_publish as delivery


def restricted_workspace(tmp_path):
    ws = Workspace(tmp_path)
    for name in ("english", "spanish", "outsider"):
        ws.source(dict(name=name, url=f"https://www.twitch.tv/{name}", kind="creator",
                       credit="@"+name, permission="review", active=True))
    with ws.store.edit() as state:
        state["settings"]["hermes_routes"] = {
            "https://twitch.tv/english": {"language": "en", "youtube_channel_id": "dankai"},
            "https://twitch.tv/spanish": {"language": "es", "youtube_channel_id": "porque"},
        }
    return ws


def test_whitelist_blocks_known_and_discovered_outsiders(tmp_path):
    ws = restricted_workspace(tmp_path)
    channels, excluded = live.configured(ws.store.read())
    assert set(channels) == {"https://twitch.tv/english", "https://twitch.tv/spanish"}
    assert "https://twitch.tv/outsider" in excluded
    assert not live.permitted(ws.store.read(), "https://kick.com/english")
    assert not live.permitted(ws.store.read(), "https://twitch.tv/random_discovery")
    desk = live.Desk(ws)
    desk.directory.mkdir()
    live.save_manifest(desk.directory/"bad.json", {"url":"https://twitch.tv/outsider", "moment":{"title":"old"}})
    assert desk.status()["moments"] == []


def test_capture_rechecks_creator_before_starting_network(tmp_path, monkeypatch):
    ws = restricted_workspace(tmp_path)
    async def fail(*args, **kwargs):
        pytest.fail("Excluded creators must not start a recorder")
    monkeypatch.setattr(live, "command", fail)
    asyncio.run(live.Desk(ws).capture("https://twitch.tv/outsider"))


def test_routes_override_old_balanced_assignments_for_unpublished_clips(tmp_path):
    ws = restricted_workspace(tmp_path)
    with ws.store.edit() as state:
        state["settings"]["hermes_youtube_channel_ids"] = ["dankai", "porque"]
        for source in state["sources"][-3:-1]:
            post = {"source_id":source["id"],"platform":"youtube", "status":"draft", "hermes_youtube_channel_id":"old"}
            state["posts"].append(post)
            expected = "dankai" if source["name"] == "english" else "porque"
            assert delivery.assign_youtube_channel(state, post) == expected


def test_community_rejects_wrong_creator_category_and_old_clips():
    import time
    from gta6.hermes_community import validate_clip, clip_url
    info = {"channel":"ohnePixel", "categories":["Grand Theft Auto VI"], "duration":20,"timestamp":time.time()}
    validate_clip(info,"ohnepixel")
    for changes in ({"channel":"outsider"},{"categories":["Counter-Strike"]},{"timestamp":time.time()-90000}):
        with pytest.raises(ValueError):
            validate_clip({**info,**changes},"ohnepixel")
    assert clip_url("https://www.twitch.tv/ohnepixel/clip/Example-Slug", "ohnepixel") == "https://clips.twitch.tv/Example-Slug"
    assert clip_url("https://www.twitch.tv/outsider/clip/Example-Slug", "ohnepixel") == ""


def test_kick_community_links_keep_the_broadcaster_and_validate_gta_vi():
    import time
    from gta6.hermes_community import clip_url, validate_clip
    expected = "https://kick.com/rdjavi/clips/clip_123ABC"
    assert clip_url(expected, "rdjavi", "kick") == expected
    assert clip_url("https://kick.com/rdjavi?clip=clip_123ABC", "rdjavi", "kick") == expected
    assert clip_url("https://kick.com/outsider?clip=clip_123ABC", "rdjavi", "kick") == ""
    validate_clip({"channel":"rdjavi", "categories":["Grand Theft Auto VI (GTA)"],
                   "duration":20, "timestamp":time.time()}, "rdjavi")


def test_translation_does_not_reuse_original_language_cache(tmp_path, monkeypatch):
    from clips import transcribe
    import types
    original = {"words":[{"start":0,"end":1,"word":"Olá"}],"language":"pt","requested_language":"pt"}
    (tmp_path/"transcript.json").write_text(json.dumps(original))
    calls=[]
    class Model:
        def __init__(self,*args,**kwargs): pass
        def transcribe(self,*args,**kwargs):
            calls.append(kwargs)
            return [], types.SimpleNamespace(language="pt",duration=1)
    monkeypatch.setitem(sys.modules,"faster_whisper",types.SimpleNamespace(WhisperModel=Model))
    monkeypatch.setattr(transcribe,"extract_audio",lambda *a,**k:"audio.wav")
    monkeypatch.setattr(transcribe,"_flatten",lambda s:([{"start":0,"end":1,"word":"Hello"}],"Hello"))
    result=transcribe.transcribe("video",str(tmp_path),language="pt",task="translate")
    assert calls[0]["task"] == "translate" and result["language"] == "en"
    assert json.loads((tmp_path/"transcript.json").read_text())["language"] == "pt"


@pytest.mark.parametrize("answer, expected", [
    ('null\n\nsession_id: local-123abc', None),
    ('```json\n{"start": 8, "end": 24}\n```\n\nsession_id: local-123abc', {"start": 8, "end": 24}),
    ('{"start": 8, "end": 24}', {"start": 8, "end": 24}),
])
def test_agent_json_allows_hermes_session_receipt(answer, expected):
    assert live.parse_agent_answer(answer) == expected


def test_agent_json_rejects_unexpected_trailing_output():
    with pytest.raises(ValueError):
        live.parse_agent_answer('{"start": 8}\n{"start": 16}')


def test_youtube_distribution_survives_restart_and_retries(tmp_path):
    ws = Workspace(tmp_path)
    with ws.store.edit() as state:
        state["settings"]["hermes_youtube_channel_ids"] = ["account-a", "account-b"]
    for index in range(10):
        # Each reservation is committed separately, as in independent render jobs.
        with Workspace(tmp_path).store.edit() as state:
            post = {"id": str(index), "platform": "youtube"}
            state["posts"].append(post)
            delivery.assign_youtube_channel(state, post)
    with ws.store.edit() as state:
        assigned = [p["hermes_youtube_channel_id"] for p in state["posts"]]
        assert assigned == ["account-a", "account-b"] * 5
        state["settings"]["hermes_youtube_channel_ids"] = ["account-c"]
        for post in state["posts"]:
            delivery.assign_youtube_channel(state, post)
        assert [p["hermes_youtube_channel_id"] for p in state["posts"]] == assigned


def test_settings_reject_duplicate_hermes_accounts(tmp_path):
    ws = Workspace(tmp_path)
    with pytest.raises(ValueError, match="distintos"):
        ws.settings({"hermes_youtube_channel_ids": ["account-a", "account-a"]})


def test_long_clip_range_and_short_community_rejection(tmp_path):
    import time
    from gta6.hermes_community import validate_clip
    ws = Workspace(tmp_path)
    ws.settings({"hermes_min_seconds": 60, "hermes_max_seconds": 120})
    assert ws.store.read()["settings"]["hermes_min_seconds"] == 60
    for changes in ({"hermes_max_seconds": 59}, {"hermes_max_seconds": 121}, {"hermes_min_seconds": True}):
        with pytest.raises(ValueError):
            ws.settings(changes)
    info = {"channel": "ohnePixel", "categories": ["Grand Theft Auto VI"],
            "duration": 60, "timestamp": time.time()}
    validate_clip(info, "ohnepixel", min_seconds=60)
    with pytest.raises(ValueError):
        validate_clip({**info, "duration": 59}, "ohnepixel", min_seconds=60)


def test_caption_cache_rejects_a_different_selected_span(tmp_path):
    manifest = tmp_path / "clip.json"
    manifest.with_suffix(".caption-language").write_text("en")
    manifest.with_suffix(".caption-model").write_text("small")
    manifest.with_suffix(".caption-layout").write_text("separate-text-v2")
    manifest.with_suffix(".caption-span").write_text("[0, 60]")
    assert live.captions_ready(manifest, {"language": "en"}, {"start": 0, "end": 60})
    assert not live.captions_ready(manifest, {"language": "en"}, {"start": 0, "end": 90})


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="requires FFmpeg")
def test_landscape_gameplay_fills_top_and_bottom_of_local_short(tmp_path):
    from gta6.hermes_previews import render_preview
    manifest = tmp_path / "sample.json"
    manifest.write_text("{}")
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=white:s=320x180:r=5:d=8",
                    "-c:v", "libx264", str(manifest.with_suffix(".ts"))], check=True)
    asyncio.run(render_preview(manifest, {"start": 0, "end": 7}))
    pixels = subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(manifest.with_suffix(".mp4")),
                                      "-vf", "scale=9:16", "-frames:v", "1", "-pix_fmt", "gray", "-f", "rawvideo", "-"])
    assert len(pixels) == 9 * 16
    assert min(pixels) > 240
    assert manifest.with_suffix(".portrait").exists()


def test_retry_after_unknown_submission_only_collects_receipt(tmp_path, monkeypatch):
    from clips import store
    ws = Workspace(tmp_path)
    source = ws.source(dict(name="Channel", url="https://kick.com/example", kind="creator",
                            credit="@example", permission="own", permission_note="Our channel"))
    post = ws.post(dict(title="Vuelta", text="Una vuelta", source_id=source["id"], commentary="Curva",
                        format="video", clip_id="clip", hook="Vuelta", pillar="gameplay", length_sec=15,
                        platform="youtube"))
    with ws.store.edit() as state:
        state["clip_jobs"] = [dict(id="job", status="done", post_ids=[post["id"]])]
    delivery.prepare(ws, "job")
    delivery.prepare(ws, "job")
    posts = ws.store.read()["posts"]
    assert sorted(p["platform"] for p in posts) == ["instagram", "tiktok", "x", "youtube"]
    media = tmp_path / "clip.mp4"
    media.touch()
    monkeypatch.setattr(store, "listing", lambda: [dict(id="clip", path=str(media))])
    uploader = Mock()
    uploader.submit.side_effect = TimeoutError()
    uploader.receipt.return_value = {"status":"published", "url":"https://www.youtube.com/shorts/example"}
    adapter = Mock()
    adapter.load.return_value = uploader
    monkeypatch.setattr(delivery, "entry_points", lambda **kw: [adapter])
    with pytest.raises(TimeoutError):
        delivery.deliver(ws, post["id"])
    delivery.deliver(ws, post["id"])
    assert uploader.submit.call_count == 1
    assert ws.store.read()["posts"][0]["status"] == "published"


def test_recording_retained_until_creator_enabled(tmp_path, monkeypatch):
    ws = Workspace(tmp_path)
    desk = live.Desk(ws)
    desk.directory.mkdir()
    m = desk.directory / "sample.json"
    live.save_manifest(m, {"url":"https://kick.com/example", "moment":{"title":"Curva"}})
    m.with_suffix(".ts").touch()
    async def once():
        work = asyncio.create_task(desk.reviews())
        await asyncio.sleep(0.05)
        work.cancel()
        with pytest.raises(asyncio.CancelledError):
            await work
    asyncio.run(once())
    assert m.exists()
    assert not ws.store.read().get("clip_jobs")


def test_pending_source_can_finish_local_cut_without_creating_delivery(tmp_path, monkeypatch):
    import time
    from gta6 import hermes_previews
    desk = live.Desk(Workspace(tmp_path))
    desk.directory.mkdir()
    manifest = desk.directory / ("a" * 32 + ".json")
    live.save_manifest(manifest, {"url": "https://kick.com/example", "captured_at": time.time(),
                                 "moment": {"start": 10, "end": 20, "title": "Vuelta"}})

    async def check():
        cut = asyncio.Event()

        async def renderer(path, moment):
            assert path == manifest and moment["end"] - moment["start"] == 10
            path.with_suffix(".mp4").write_bytes(b"preview")
            path.with_suffix(".portrait").touch()
            cut.set()

        monkeypatch.setattr(hermes_previews, "render_preview", renderer)
        worker = asyncio.create_task(hermes_previews.preview_loop(desk))
        try:
            await asyncio.wait_for(cut.wait(), timeout=3)
            snapshot = desk.status()
            assert snapshot["moments"][0]["preview_ready"]
            assert not snapshot["deliveries"]
            assert not desk.workspace.store.read().get("clip_jobs")
        finally:
            worker.cancel()
            with pytest.raises(asyncio.CancelledError):
                await worker

    asyncio.run(check())
