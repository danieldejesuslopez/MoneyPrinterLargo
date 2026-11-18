"""Discovery boundaries and identity without contacting creator platforms."""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gta6 import discovery


VIDEO_ID = "abcdefghijk"
CHANNEL_ID = "UC" + "a" * 22
SOURCE = {"name": "Creador", "url": "https://www.youtube.com/@creador"}


def video(**changes):
    return {"id": VIDEO_ID, "title": "Carreras GTA V", "timestamp": 1794916800, "duration": 300,
            "url": "https://www.youtube.com/watch?v=" + VIDEO_ID, **changes}


def reply(monkeypatch, metadata, *, returncode=0, stderr=""):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(stdout=json.dumps(metadata), stderr=stderr, returncode=returncode)

    monkeypatch.setattr(discovery.subprocess, "run", run)
    return calls


def test_youtube_listing_is_bounded_public_and_does_not_download(monkeypatch):
    calls = reply(monkeypatch, {"channel_id": CHANNEL_ID, "uploader_id": "@creador", "entries": [video()]})
    result = discovery.discover_source(SOURCE, limit=3, timeout=12)
    assert result["status"] == "ok"
    assert len(result["videos"]) == 1
    row = result["videos"][0]
    assert row["published_at"] == "2026-11-17T12:00:00+00:00"
    assert row["channel_id"] == CHANNEL_ID
    command, options = calls[0]
    assert command[:3] == [sys.executable, "-m", "yt_dlp"]
    assert command[-1] == "https://www.youtube.com/@creador/videos"
    assert command[command.index("--playlist-end") + 1] == "3"
    assert "--ignore-config" in command and "--skip-download" in command
    assert "--flat-playlist" in command
    assert "youtubetab:approximate_date=true" in command
    assert options["timeout"] == 12
    assert not any("cookie" in value or "browser" in value for value in command)


def test_watch_short_and_share_url_variants_are_one_video(monkeypatch):
    entries = [video(), video(id="ignored", url=f"https://youtu.be/{VIDEO_ID}?t=35"),
               video(url=f"https://www.youtube.com/shorts/{VIDEO_ID}")]
    reply(monkeypatch, {"entries": entries})
    result = discovery.discover_source(SOURCE)
    assert len(result["videos"]) == 1
    assert result["videos"][0]["url"] == f"https://www.youtube.com/watch?v={VIDEO_ID}"
    assert discovery.canonical_video_key(result["videos"][0]) == f"youtube:{VIDEO_ID}"


def test_partial_playlist_failure_keeps_usable_metadata_and_visible_error(monkeypatch):
    reply(monkeypatch, {"entries": [None, video(), None]}, returncode=1, stderr="ERROR: one video is private")
    result = discovery.discover_source(SOURCE)
    assert result["status"] == "ok"
    assert len(result["videos"]) == 1
    assert "private" in result["error"]


@pytest.mark.parametrize("url", ["https://127.0.0.1/", "http://localhost/a", "https://youtube.com.evil.test/@x", "file:///tmp/video.mp4"])
def test_unsupported_or_local_sources_never_start_a_process(monkeypatch, url):
    calls = reply(monkeypatch, {})
    result = discovery.discover_source({"url": url})
    assert result["status"] in ("unsupported", "error")
    assert result["error"] and result["videos"] == [] and calls == []


@pytest.mark.parametrize("url", ["https://user:secret@youtube.com/@creator", "https://youtube.com:8000/@creator", "https://youtube.com/@creator%2fwatch"])
def test_credentials_ports_and_handle_mismatch_are_rejected(monkeypatch, url):
    calls = reply(monkeypatch, {"uploader_id": "@other", "entries": [video()]})
    result = discovery.discover_source({"url": url})
    assert result["status"] == "error" and result["videos"] == []
    if "secret" in url or ":8000" in url:
        assert not calls


def test_timeout_is_caught_and_reported_per_source(monkeypatch):
    def run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(discovery.subprocess, "run", run)
    result = discovery.discover_source(SOURCE, timeout=3)
    assert result["status"] == "error" and not result["videos"]
    assert "3 segundos" in result["error"]


def test_invalid_json_is_an_explicit_error(monkeypatch):
    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="not json", stderr="blocked", returncode=1))
    result = discovery.discover_source(SOURCE)
    assert result == {"videos": [], "status": "error", "error": "blocked"}


def test_empty_public_channel_is_not_an_error(monkeypatch):
    reply(monkeypatch, {"entries": []})
    assert discovery.discover_source(SOURCE) == {"videos": [], "status": "ok", "error": ""}


def test_twitch_profiles_target_recorded_archives(monkeypatch):
    calls = reply(monkeypatch, {"id": "rubius", "entries": [{"id": "v12345", "title": "GTA RP", "upload_date": "20261117"}]})
    result = discovery.discover_source({"url": "https://www.twitch.tv/rubius"})
    assert calls[0][0][-1] == "https://www.twitch.tv/rubius/videos?filter=archives&sort=time"
    assert result["videos"][0]["url"] == "https://www.twitch.tv/videos/12345"
    assert result["videos"][0]["published_at"] == "2026-11-17T00:00:00+00:00"


def test_kick_channel_support_is_not_claimed_without_an_archive_extractor(monkeypatch):
    calls = reply(monkeypatch, {})
    result = discovery.discover_source({"url": "https://kick.com/lordkebun"})
    assert result["status"] == "unsupported" and not calls
    assert "seguimiento" in result["error"]


def test_monitor_url_overrides_profile_for_discovery(monkeypatch):
    calls = reply(monkeypatch, {"entries": [video()]})
    result = discovery.discover_source({"url": "https://kick.com/creador", "monitor_url": SOURCE["url"]})
    assert result["status"] == "ok"
    assert calls[0][0][-1] == "https://www.youtube.com/@creador/videos"


@pytest.mark.parametrize("title", ["GTA 5 parkour", "GTA VI novedades", "GTA6", "GTAV Online", "Grand Theft Auto V", "NoPixel: robo fallido", "Marbella Vice #3"])
def test_recognizes_gta_terms(title):
    assert discovery.is_gta_video({"title": title})


@pytest.mark.parametrize("changes", [
    {"title": "Minecraft con amigos"}, {"title": "GTA 6 leaked gameplay"},
    {"title": "GTA VI filtraciones"}, {"title": "GTA 5", "is_live": True},
    {"title": "GTA 5", "live_status": "is_upcoming"}, {"title": "Mi servidor RP de Minecraft"},
])
def test_does_not_treat_unrelated_gaming_leaks_or_active_streams_as_candidates(changes):
    assert not discovery.is_gta_video(changes)


def test_missing_date_gets_details_only_for_relevant_videos(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        metadata = {"channel_id": CHANNEL_ID, "entries": [video(timestamp=None), video(id="mnopqrstuvw", url="https://youtu.be/mnopqrstuvw", title="Minecraft", timestamp=None)]}
        if "--flat-playlist" not in command:
            metadata = video(upload_date="20261117", timestamp=None, channel_id=CHANNEL_ID)
        return SimpleNamespace(stdout=json.dumps(metadata), stderr="", returncode=0)

    monkeypatch.setattr(discovery.subprocess, "run", run)
    result = discovery.discover_source(SOURCE)
    assert len(calls) == 2
    assert "--no-playlist" in calls[1]
    assert result["videos"][0]["published_at"] == "2026-11-17T00:00:00+00:00"
    assert result["videos"][1]["published_at"] == ""


def test_hydration_honors_one_total_source_deadline(monkeypatch):
    clock = iter([0.0, 40.0, 45.0])
    monkeypatch.setattr(discovery.time, "monotonic", lambda: next(clock))
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        metadata = {"entries": [video(timestamp=None), video(id="mnopqrstuvw", url="https://youtu.be/mnopqrstuvw", timestamp=None)]}
        if "--flat-playlist" not in command:
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return SimpleNamespace(stdout=json.dumps(metadata), stderr="", returncode=0)

    monkeypatch.setattr(discovery.subprocess, "run", run)
    result = discovery.discover_source(SOURCE, timeout=45)
    assert len(calls) == 2
    assert calls[1][1]["timeout"] == 5
    assert len(result["videos"]) == 2
    assert "5 segundos" in result["error"]
    assert all(not row["published_at"] for row in result["videos"])


def test_foreign_channel_entries_and_detail_identity_are_excluded(monkeypatch):
    reply(monkeypatch, {"channel_id": CHANNEL_ID, "entries": [video(channel_id="UC" + "b" * 22)]})
    result = discovery.discover_source(SOURCE)
    assert not result["videos"]
    assert result["status"] == "error" and "otro canal" in result["error"]


def test_foreign_source_channel_identity_is_an_error(monkeypatch):
    reply(monkeypatch, {"channel_id": "UC" + "b" * 22, "entries": [video()]})
    result = discovery.discover_source({"url": "https://www.youtube.com/channel/" + CHANNEL_ID})
    assert result["status"] == "error" and not result["videos"]


def test_unknown_date_and_invalid_duration_stay_unknown(monkeypatch):
    reply(monkeypatch, {"entries": [video(title="Minecraft", timestamp=None, upload_date="invalid", duration=float("nan"))]})
    row = discovery.discover_source(SOURCE)["videos"][0]
    assert row["published_at"] == "" and row["duration"] is None


@pytest.mark.parametrize("limit,timeout", [(0, 45), (31, 45), (True, 45), (5, 0), (5, float("inf"))])
def test_invalid_bounds_are_rejected(limit, timeout):
    with pytest.raises(ValueError):
        discovery.discover_source(SOURCE, limit=limit, timeout=timeout)
