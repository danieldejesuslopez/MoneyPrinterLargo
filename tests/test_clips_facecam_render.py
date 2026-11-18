"""Automatic face panels keep the main framing and respect manual boxes."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from clips import faces, render  # noqa: E402
from clips.segments import Word  # noqa: E402


@pytest.fixture
def renderer(monkeypatch, tmp_path):
    settings = {**render.clips_config.DEFAULTS, "auto_facecam": True}
    commands = []
    scans = []
    output = tmp_path / "clip.mp4"
    state = SimpleNamespace(track=None)

    def scan(*args, **kwargs):
        scans.append((args, kwargs))
        return state.track

    def encode(command, **kwargs):
        commands.append(command)
        (Path(kwargs["cwd"]) / command[-1]).touch()
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(render.clips_config, "setting", settings.get)
    monkeypatch.setattr(render.clips_config, "fonts_dir", lambda: "")
    monkeypatch.setattr(render.clips_config, "font_path", lambda: "")
    monkeypatch.setattr(render, "probe", lambda path: {"width": 1280, "height": 720})
    monkeypatch.setattr(render, "_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(render, "encoder_chain", lambda: [("libx264", ["-c:v", "libx264"])])
    monkeypatch.setattr(render.subprocess, "run", encode)
    monkeypatch.setattr(faces, "scan_faces", scan)
    return SimpleNamespace(
        settings=settings, commands=commands, scans=scans, output=output, state=state
    )


def track():
    return faces.FaceTrack(
        crop_w=360,
        crop_h=200,
        points=((0.0, 850, 20), (2.0, 880, 30)),
        intervals=((0.0, 4.0),),
    )


def command_graph(command):
    return command[command.index("-filter_complex") + 1]


@pytest.mark.parametrize("layout", ["blur", "crop"])
def test_no_face_preserves_the_selected_render_graph(renderer, layout):
    metadata = {}
    render.render_clip(
        "fragment.mp4", 12.25, 4.0, str(renderer.output),
        subtitles=False, layout=layout, render_info=metadata,
    )
    assert renderer.scans == (
        [(("fragment.mp4", 12.25, 4.0, (1280, 720)), {
            "panel_aspect": 1.8, "visible_region": (437.5, 0.0, 405.0, 720.0),
        })] if layout == "crop" else []
    )
    command = renderer.commands[0]
    assert command[command.index("-ss") + 1] == "12.250"
    assert command_graph(command) == render._filter_graph(1080, 1920, layout)
    assert metadata == {"layout": layout, "main_layout": layout, "auto_facecam": False}


def test_clipped_face_panel_keeps_the_existing_main_scale_and_crop(renderer):
    layout = "crop"
    renderer.state.track = track()
    metadata = {}
    render.render_clip(
        "fragment.mp4", 0.0, 4.0, str(renderer.output),
        subtitles=False, layout=layout, render_info=metadata,
    )
    graph = command_graph(renderer.commands[0])
    original = render._filter_graph(1080, 1920, layout)
    assert original.replace("[0:v]", "[main]").replace("[v]", "[mainv]") in graph
    assert "[tracked]crop=360:200:" in graph
    assert "[mainv][facev]overlay=0:0:" in graph
    assert "vstack" not in graph
    assert metadata == {"layout": "facecam", "main_layout": layout, "auto_facecam": True}


def test_full_frame_never_duplicates_a_face_even_when_the_detector_would_find_one(renderer):
    renderer.state.track = track()
    metadata = {}
    render.render_clip(
        "fragment.mp4", 0.0, 4.0, str(renderer.output),
        subtitles=False, layout="blur", render_info=metadata,
    )
    assert renderer.scans == []
    assert command_graph(renderer.commands[0]) == render._filter_graph(1080, 1920, "blur")
    assert metadata["auto_facecam"] is False


@pytest.mark.parametrize(
    "source_size, expected",
    [
        ((1280, 720), (437.5, 0, 405, 720)),
        ((1080, 1920), (0, 0, 1080, 1920)),
        ((720, 1920), (0, 320, 720, 1280)),
    ],
)
def test_visible_source_region_matches_the_unchanged_centre_crop(source_size, expected):
    assert render.visible_source_region(1080, 1920, source_size) == pytest.approx(expected)


def test_face_panel_places_title_and_subtitles_below_the_face(renderer):
    renderer.state.track = track()
    render.render_clip(
        "fragment.mp4", 0.0, 4.0, str(renderer.output),
        layout="crop",
        words=[Word(0.0, 0.5, "hola")], title="MI TÍTULO", title_overlay="always",
    )
    rows = renderer.output.with_suffix(".ass").read_text(encoding="utf-8").splitlines()
    subtitle = next(row.split(",") for row in rows if row.startswith("Style: Clip,"))
    title = next(row.split(",") for row in rows if row.startswith("Style: Title,"))
    assert int(subtitle[-5]) == 2  # subtitle anchored from the bottom
    assert 600 < 1920 - int(subtitle[-2]) < 1920
    assert int(title[-5]) == 8  # title starts below the face
    assert int(title[-2]) == 600 + int(1920 * 0.035)
    title_bottom = int(title[-2]) + int(title[2]) * 1.25
    subtitle_top = 1920 - int(subtitle[-2]) - int(subtitle[2]) * 1.25 * 2
    assert title_bottom + 40 < subtitle_top
    assert 1920 - int(subtitle[-2]) < 1920 * 0.80  # clear of source dialogue at the bottom
    graph = command_graph(renderer.commands[0])
    assert graph.index("overlay=0:0:") < graph.index("subtitles=")


def test_manual_camera_box_has_priority_over_scanning(renderer):
    metadata = {}
    render.render_clip(
        "fragment.mp4", 0.0, 4.0, str(renderer.output),
        subtitles=False, layout="facecam", facecam="898,11,360,200", render_info=metadata,
    )
    assert renderer.scans == []
    graph = command_graph(renderer.commands[0])
    assert "crop=360:200:898:11" in graph
    assert "vstack=inputs=2" in graph
    assert metadata["auto_facecam"] is False


def test_empty_camera_box_keeps_full_frame_without_duplicating_a_visible_face(renderer):
    renderer.state.track = track()
    render.render_clip(
        "fragment.mp4", 5.0, 4.0, str(renderer.output),
        subtitles=False, layout="facecam", facecam="",
    )
    assert renderer.scans == []
    assert command_graph(renderer.commands[0]) == render._filter_graph(1080, 1920, "blur")


def test_disabling_automatic_facecam_does_not_scan(renderer):
    renderer.settings["auto_facecam"] = False
    render.render_clip(
        "fragment.mp4", 0.0, 4.0, str(renderer.output), subtitles=False,
    )
    assert renderer.scans == []
    assert command_graph(renderer.commands[0]) == render._filter_graph(1080, 1920, "blur")


def test_detector_failure_is_a_render_error_before_encoding(renderer, monkeypatch):
    def unavailable(*args, **kwargs):
        raise faces.FaceDetectionError("Install opencv-python-headless")

    monkeypatch.setattr(faces, "scan_faces", unavailable)
    with pytest.raises(render.RenderError, match="opencv-python-headless"):
        render.render_clip("fragment.mp4", 0.0, 4.0, str(renderer.output), layout="crop")
    assert renderer.commands == []


def test_manual_camera_outside_the_source_is_rejected(renderer):
    with pytest.raises(render.RenderError, match="beyond the source"):
        render.render_clip(
            "fragment.mp4", 0.0, 4.0, str(renderer.output),
            layout="facecam", facecam="1200,11,360,200",
        )
    assert renderer.scans == []
    assert renderer.commands == []


def test_api_allows_automatic_facecam_without_a_manual_box(monkeypatch):
    sys.path.insert(0, str(ROOT / "webapp" / "api"))
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sse_starlette.sse import AppStatus
    import clips_api

    spawned = []

    def spawn(args, **kwargs):
        spawned.append(args)
        return object()

    async def stream(job):
        yield {"event": "done", "data": "{}"}

    monkeypatch.setattr(clips_api, "_spawn_job", spawn)
    monkeypatch.setattr(clips_api, "_stream_job", stream)
    AppStatus.should_exit_event = None
    app = FastAPI()
    app.include_router(clips_api.router)
    response = TestClient(app).get(
        "/api/clips/run",
        params={"target": "https://www.youtube.com/watch?v=abc", "layout": "facecam"},
    )
    assert response.status_code == 200
    assert spawned[0][spawned[0].index("--layout") + 1] == "facecam"
    assert "--facecam" not in spawned[0]
