"""Clipping a stream that is still on air: fragment arithmetic and wiring.

No network here. The fragment maths decides which five-second pieces of a
stream are fetched for a clip, and an off-by-one there either clips the
first words of a span or fetches minutes nobody asked for.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clips import ingest, live  # noqa: E402


# -- Which fragments ---------------------------------------------------


def test_whole_stream_when_under_the_cap():
    assert live.sequence_range(1629, 5.0, 180) == (0, 1629)


def test_only_the_newest_minutes_past_the_cap():
    first, last = live.sequence_range(1629, 5.0, 3)
    assert last == 1629
    assert last - first + 1 == 36  # 3 min of 5 s fragments


def test_no_cap_means_everything():
    assert live.sequence_range(10, 5.0, None) == (0, 10)


def test_span_fragments_cover_the_span_with_a_margin():
    first, last = live.span_sequences(60.0, 80.0, 5.0, 1630, 1665)
    # 60 s into a snapshot that starts at fragment 1630 is fragment 1642;
    # one fragment of margin each side.
    assert (first, last) == (1641, 1647)
    covered_from = (first - 1630) * 5.0
    covered_to = (last - 1630 + 1) * 5.0
    assert covered_from <= 60.0 and covered_to >= 80.0


def test_span_fragments_never_leave_the_snapshot():
    assert live.span_sequences(0.0, 10.0, 5.0, 100, 200)[0] == 100
    assert live.span_sequences(990.0, 1000.0, 5.0, 0, 199)[1] == 199


# -- Fetching ----------------------------------------------------------


def test_revoked_urls_are_refreshed_once_and_the_fetch_continues(tmp_path, monkeypatch):
    """YouTube revokes signed URLs mid-fetch; one refresh must rescue it."""
    refreshed = []

    def fake_get(fmt, sq):
        if fmt["url"] == "old" and sq >= 3:
            raise live.LiveAuthError(f"fragment {sq}: HTTP 401")
        return f"[{sq}]".encode(), {}

    monkeypatch.setattr(live, "_get", fake_get)

    def refresh():
        refreshed.append(1)
        return {"url": "new"}

    out = tmp_path / "joined.m4a"
    live.fetch_range({"url": "old"}, 0, 5, str(out), refresh=refresh)
    assert out.read_bytes() == b"[0][1][2][3][4][5]"
    assert 1 <= len(refreshed) <= 2  # threads racing may each see one revocation


def test_without_refresh_a_revoked_url_fails_loudly(tmp_path, monkeypatch):
    monkeypatch.setattr(
        live, "_get", lambda fmt, sq: (_ for _ in ()).throw(live.LiveAuthError("401"))
    )
    with pytest.raises(live.LiveAuthError):
        live.fetch_range({"url": "old"}, 0, 2, str(tmp_path / "x"))


def test_cached_fragments_are_not_fetched_again(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    for sq in range(0, 3):
        (cache / f"{sq:08d}").write_bytes(f"[{sq}]".encode())
    fetched = []

    def fake_get(fmt, sq):
        fetched.append(sq)
        return f"[{sq}]".encode(), {}

    monkeypatch.setattr(live, "_get", fake_get)
    out = tmp_path / "joined.m4a"
    live.fetch_range({"url": "u"}, 0, 4, str(out), cache_dir=str(cache))
    assert sorted(fetched) == [3, 4]
    assert out.read_bytes() == b"[0][1][2][3][4]"
    assert (cache / "00000004").exists()  # the cache is kept for next time


# -- Which formats -----------------------------------------------------


def fmt(fid, vcodec, acodec, ext, height=None, abr=None, tbr=None, protocol="http_dash_segments_generator"):
    return {
        "format_id": fid, "vcodec": vcodec, "acodec": acodec, "ext": ext,
        "height": height, "abr": abr, "tbr": tbr, "protocol": protocol,
        "url": f"https://x/{fid}", "target_duration": 5,
    }


INFO = {
    "title": "GRAN FINAL", "uploader": "IbaiExtra", "is_live": True,
    "formats": [
        fmt("139", "none", "mp4a", "m4a", abr=64),
        fmt("140", "none", "mp4a", "m4a", abr=144),
        fmt("135", "avc1", "none", "mp4", height=480, tbr=1077),
        fmt("298", "avc1", "none", "mp4", height=720, tbr=2639),
        fmt("299", "avc1", "none", "mp4", height=1080, tbr=5144),
        fmt("302", "vp9", "none", "webm", height=720, tbr=1276),
        fmt("95", "avc1", "mp4a", "mp4", height=720, protocol="m3u8_native"),
    ],
}


def test_picks_best_m4a_audio_and_720p_mp4_video():
    picked = live.pick_formats(INFO)
    assert picked["audio"]["format_id"] == "140"
    assert picked["video"]["format_id"] == "298"
    assert picked["target"] == 5.0
    assert picked["is_live"] is True


def test_hls_only_streams_are_refused_with_a_reason():
    hls_only = {"formats": [fmt("95", "avc1", "mp4a", "mp4", 720, protocol="m3u8_native")]}
    with pytest.raises(live.LiveError, match="DVR"):
        live.pick_formats(hls_only)


def test_low_resolution_stream_still_gets_a_video_format():
    small = {"formats": [fmt("140", "none", "mp4a", "m4a", abr=144),
                         fmt("134", "avc1", "none", "mp4", height=360)]}
    assert live.pick_formats(small)["video"]["format_id"] == "134"


# -- Manifest and wiring -----------------------------------------------


def test_ordinary_sources_are_their_own_span():
    source = ingest.Source(id="x", path="/videos/a.mp4")
    assert ingest.materialize_span(source, 10.0, 20.0) == ("/videos/a.mp4", 0.0)


def test_live_fields_survive_the_manifest(tmp_path):
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"x")
    source = ingest.Source(
        id="url-1-live-202611141600", path=str(audio), url="https://y", live=True,
        live_first_sq=1630, live_last_sq=1665, live_target=5.0, live_start_time=8146.05,
    )
    ingest.save_manifest(source)
    loaded = ingest.load_manifest(str(tmp_path))
    assert loaded.live is True
    assert (loaded.live_first_sq, loaded.live_last_sq) == (1630, 1665)
    assert loaded.live_start_time == pytest.approx(8146.05)


def test_a_cached_live_piece_is_reused_without_fetching(tmp_path, monkeypatch):
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"x")
    source = ingest.Source(id="s", path=str(audio), url="https://y", live=True,
                           live_first_sq=0, live_last_sq=99, live_target=5.0)
    piece = tmp_path / "piece-600-800.mp4"
    piece.write_bytes(b"x")
    (tmp_path / "piece-600-800.mp4.json").write_text(json.dumps({"offset": 55.0}))
    monkeypatch.setattr(live, "live_formats", lambda url: pytest.fail("should not refetch"))
    path, offset = ingest.materialize_span(source, 60.0, 80.0)
    assert path == str(piece)
    assert offset == pytest.approx(55.0)
