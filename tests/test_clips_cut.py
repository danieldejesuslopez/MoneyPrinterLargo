"""Hand-picked cuts, caption subtitles and the facecam layout.

Pure pieces only: span parsing, word snapping, caption parsing and the
facecam geometry. The ffmpeg graph is checked as a string, which is what
catches a broken layout before a render does.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clips import pipeline, render, transcribe  # noqa: E402
from clips.pipeline import ClipError  # noqa: E402
from clips.segments import Word  # noqa: E402


# -- Spans -------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("1:32-2:03", (92.0, 123.0)),
        ("92-123", (92.0, 123.0)),
        ("0:01:32-0:02:03", (92.0, 123.0)),
        (" 8:05 - 8:50 ", (485.0, 530.0)),
        ("0:13.5-0:20", (13.5, 20.0)),
    ],
)
def test_spans_parse_in_every_written_form(text, expected):
    assert pipeline.parse_span(text) == pytest.approx(expected)


@pytest.mark.parametrize("text", ["2:03-1:32", "1:32", "a-b", "1:32-", "1::2-3"])
def test_bad_spans_are_errors_not_guesses(text):
    with pytest.raises(ClipError):
        pipeline.parse_span(text)


def test_snap_widens_to_whole_words_and_never_trims():
    words = [Word(9.5, 10.5, "cut"), Word(19.8, 20.6, "here")]
    assert pipeline.snap_span(words, 10.0, 20.0) == pytest.approx((9.5, 20.6))


def test_snap_leaves_clean_edges_alone():
    words = [Word(11.0, 12.0, "inside")]
    assert pipeline.snap_span(words, 10.0, 20.0) == (10.0, 20.0)


# -- Captions ----------------------------------------------------------


VTT = """WEBVTT
Kind: captions
Language: es

00:00:15.840 --> 00:00:17.840
Otra vez el vecino aparcado aqui?!

00:00:42.000 --> 00:00:44.440
*rie*

00:01:48.000 --> 00:01:52.000
Dios! Vienen! Vienen! (x10)

00:01:53.000 --> 00:01:55.000
<c>sal</c> cagando x4

00:02:00.000 --> 00:02:01.000

"""


def test_vtt_cues_become_evenly_spread_words(tmp_path):
    path = tmp_path / "video.es.vtt"
    path.write_text(VTT, encoding="utf-8")
    words = transcribe.words_from_captions(str(path))
    first = [w for w in words if w["start"] < 18]
    assert [w["word"] for w in first] == ["Otra", "vez", "el", "vecino", "aparcado", "aqui?!"]
    assert first[0]["start"] == pytest.approx(15.84)
    assert first[-1]["end"] == pytest.approx(17.84)


def test_caption_annotations_are_not_burned_in_as_speech(tmp_path):
    path = tmp_path / "video.es.vtt"
    path.write_text(VTT, encoding="utf-8")
    text = " ".join(w["word"] for w in transcribe.words_from_captions(str(path)))
    assert "rie" not in text
    assert "(x10)" not in text and "x10" not in text
    assert "x4" not in text
    assert "<c>" not in text
    assert "Dios! Vienen! Vienen!" in text
    assert "sal cagando" in text


def test_srt_is_read_too(tmp_path):
    path = tmp_path / "video.srt"
    path.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nHola mundo\n\n", encoding="utf-8"
    )
    words = transcribe.words_from_captions(str(path))
    assert [w["word"] for w in words] == ["Hola", "mundo"]
    assert words[0]["start"] == pytest.approx(1.0)


# -- Facecam layout ----------------------------------------------------


def test_box_parsing():
    assert render.parse_box("898,11,360,200") == (898, 11, 360, 200)
    assert render.parse_box([898, 11, 360, 200]) == (898, 11, 360, 200)
    assert render.parse_box("") is None
    assert render.parse_box("1,2,3") is None
    assert render.parse_box("1,2,0,4") is None
    assert render.parse_box("a,b,c,d") is None


def test_facecam_panels_fill_the_frame_exactly():
    geo = render.facecam_geometry(1080, 1920, 1280, 720, (898, 11, 360, 200))
    assert geo["cam_h"] + geo["game_h"] == 1920
    assert geo["cam_h"] % 2 == 0 and geo["crop_w"] % 2 == 0


def test_gameplay_crop_keeps_the_panel_aspect_and_avoids_the_camera():
    geo = render.facecam_geometry(1080, 1920, 1280, 720, (898, 11, 360, 200))
    panel_aspect = 1080 / geo["game_h"]
    assert geo["crop_w"] / geo["crop_h"] == pytest.approx(panel_aspect, rel=0.01)
    # The face is shown once, in its own panel: the game crop stops before it.
    assert geo["crop_x"] + geo["crop_w"] <= 898
    assert geo["crop_x"] >= 0


def test_gameplay_crop_stays_centred_when_the_camera_is_out_of_the_way():
    geo = render.facecam_geometry(1080, 1920, 1280, 720, (0, 600, 200, 110))
    assert geo["crop_x"] == (1280 - geo["crop_w"]) // 2


def test_facecam_graph_stacks_camera_over_game():
    graph = render._filter_graph(
        1080, 1920, "facecam", facecam=(898, 11, 360, 200), source_size=(1280, 720)
    )
    assert "crop=360:200:898:11" in graph
    assert "vstack=inputs=2" in graph
    assert graph.endswith("[v]")


def test_facecam_without_a_box_falls_back_to_the_full_frame():
    graph = render._filter_graph(1080, 1920, "facecam")
    assert "vstack" not in graph
    assert "gblur" in graph


def test_subtitles_can_move_to_the_seam(tmp_path):
    out = tmp_path / "subs.ass"
    render.write_ass([Word(0.0, 0.5, "hola")], str(out), 1080, 1920, margin_v=1090)
    style = next(line for line in out.read_text(encoding="utf-8").splitlines()
                 if line.startswith("Style: Clip"))
    assert style.endswith(",1090,1")
