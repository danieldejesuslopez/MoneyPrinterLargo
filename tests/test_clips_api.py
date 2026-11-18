"""The webapp's clips router: validation and the command line it builds.

The router is mounted on a bare FastAPI app with a fake job system, so
these tests never spawn a process, touch ffmpeg, or read the real
catalogue's videos. What matters here is what reaches `scripts/clips.py`.
"""

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "webapp" / "api"))

import clips_api  # noqa: E402


@pytest.fixture
def spawned(monkeypatch):
    """Capture the args a run would spawn, and answer with an empty stream."""
    calls = []

    def fake_spawn(args, title="", **kwargs):
        calls.append({"args": args, "title": title, **kwargs})
        return object()

    async def fake_stream(job):
        yield {"event": "done", "data": "{}"}

    monkeypatch.setattr(clips_api, "_spawn_job", fake_spawn)
    monkeypatch.setattr(clips_api, "_stream_job", fake_stream)
    return calls


@pytest.fixture
def client():
    # sse_starlette keeps a module-level exit event bound to the first event
    # loop that used it; each TestClient runs a new loop, so reset it.
    from sse_starlette.sse import AppStatus

    AppStatus.should_exit_event = None
    app = FastAPI()
    app.include_router(clips_api.router)
    return TestClient(app)


URL = "https://www.youtube.com/watch?v=abc"


def test_a_url_without_a_rights_note_gets_the_webapp_default(client, spawned):
    """The page has no rights field, so a URL run records a default note."""
    response = client.get("/api/clips/run", params={"target": URL})
    assert response.status_code == 200
    args = spawned[0]["args"]
    assert args[args.index("--rights") + 1] == clips_api.DEFAULT_RIGHTS


def test_a_missing_local_file_is_refused(client, spawned):
    response = client.get("/api/clips/run", params={"target": "C:/nope/missing.mp4"})
    assert response.status_code == 400
    assert spawned == []


def test_a_bad_span_is_refused_before_anything_runs(client, spawned):
    response = client.get(
        "/api/clips/run",
        params={"target": URL, "rights": "mine", "mode": "cut", "spans": "2:03-1:32"},
    )
    assert response.status_code == 400
    assert spawned == []


def test_bad_layout_and_facecam_box_are_refused(client, spawned):
    base = {"target": URL, "rights": "mine"}
    assert client.get("/api/clips/run", params={**base, "layout": "zoom"}).status_code == 400
    bad_box = {**base, "layout": "facecam", "facecam": "1,2,3"}
    assert client.get("/api/clips/run", params=bad_box).status_code == 400
    assert spawned == []


def test_auto_mode_builds_the_run_command(client, spawned):
    response = client.get(
        "/api/clips/run",
        params={
            "target": URL,
            "rights": "mi canal",
            "count": 4,
            "language": "es",
            "layout": "facecam",
            "facecam": "898,11,360,200",
            "use_llm": "false",
        },
    )
    assert response.status_code == 200
    args = spawned[0]["args"]
    assert args[:3] == ["clips", "run", URL]
    assert args[args.index("--count") + 1] == "4"
    assert "--no-llm" in args
    assert args[args.index("--rights") + 1] == "mi canal"
    assert args[args.index("--facecam") + 1] == "898,11,360,200"


def test_count_is_clamped(client, spawned):
    client.get("/api/clips/run", params={"target": URL, "rights": "x", "count": 99})
    args = spawned[0]["args"]
    assert args[args.index("--count") + 1] == "10"


def test_titles_stay_paired_with_their_spans(client, spawned):
    """A span without a title must not shift later titles onto it."""
    client.get(
        "/api/clips/run",
        params={
            "target": URL,
            "rights": "x",
            "mode": "cut",
            "spans": "0:42-1:03;2:16-2:42;4:10-4:48",
            "titles": "PRIMERO||TERCERO",
        },
    )
    args = spawned[0]["args"]
    titles = [args[i + 1] for i, a in enumerate(args) if a == "--title"]
    spans = [args[i + 1] for i, a in enumerate(args) if a == "--at"]
    assert spans == ["0:42-1:03", "2:16-2:42", "4:10-4:48"]
    assert titles == ["PRIMERO", "", "TERCERO"]


def test_no_titles_means_no_title_flags(client, spawned):
    client.get(
        "/api/clips/run",
        params={"target": URL, "rights": "x", "mode": "cut", "spans": "0:42-1:03"},
    )
    assert "--title" not in spawned[0]["args"]


def test_subtitle_choices_map_to_cli_flags(client, spawned):
    client.get(
        "/api/clips/run",
        params={"target": URL, "rights": "x", "subtitles": "false", "whisper": "true"},
    )
    args = spawned[0]["args"]
    assert "--no-subtitles" in args and "--whisper" in args


def test_runs_do_not_offer_a_youtube_upload(client, spawned):
    """No channel/kind on the job: that is what shows "Subir a YouTube"."""
    client.get("/api/clips/run", params={"target": URL, "rights": "x"})
    assert "channel_id" not in spawned[0] and "kind" not in spawned[0]


def test_unknown_status_is_refused(client):
    assert client.patch("/api/clips/whatever", json={"status": "viral"}).status_code == 400
