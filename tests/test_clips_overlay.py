"""The burned-in title: when it appears, where it sits, how it is written.

A clip with no speech has nothing on screen to read; the title fills
that gap. Where it lands matters as much as whether it appears: over the
face in a facecam layout, or over the game in a blur layout, it hides the
thing people came to watch.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clips import render  # noqa: E402
from clips.segments import Word  # noqa: E402


# -- When --------------------------------------------------------------


@pytest.mark.parametrize(
    "mode, has_subtitles, expected",
    [
        ("auto", False, True),
        ("auto", True, False),
        ("always", True, True),
        ("always", False, True),
        ("never", False, False),
        ("never", True, False),
        ("", False, True),  # unset behaves like auto
        ("AUTO", False, True),
    ],
)
def test_title_burn_policy(mode, has_subtitles, expected):
    assert render.should_burn_title(mode, has_subtitles, "GRAND SLAM") is expected


def test_no_title_means_nothing_to_burn():
    assert render.should_burn_title("always", False, "   ") is False


# -- Where -------------------------------------------------------------


TITLE = "BASES LLENAS Y GRAND SLAM PERFECTO"


def test_blur_title_sits_inside_the_top_band():
    """A 16:9 source in 9:16 leaves an empty band above the video."""
    alignment, margin = render.title_placement(
        "blur", 1080, 1920, (1276, 720), TITLE, 88
    )
    assert alignment == 8  # top centre
    video_h = 1080 * 720 / 1276
    band = (1920 - video_h) / 2
    chars_per_line = int(1080 * 0.86 / (88 * 0.55))
    lines = -(-len(TITLE) // chars_per_line)
    block = lines * 88 * 1.25
    assert margin >= 40
    # The whole block ends before the video begins.
    assert margin + block <= band


def test_blur_title_is_roughly_centred_in_the_band():
    _, margin = render.title_placement("blur", 1080, 1920, (1920, 1080), "SHORT", 88)
    band = (1920 - 1080 * 1080 / 1920) / 2
    block = 88 * 1.25
    assert margin == pytest.approx((band - block) / 2, abs=1)


def test_longer_titles_wrap_to_more_lines_and_start_higher():
    _, short = render.title_placement("blur", 1080, 1920, (1276, 720), "GOL", 88)
    _, long = render.title_placement("blur", 1080, 1920, (1276, 720), TITLE * 2, 88)
    assert long < short


def test_crop_title_goes_near_the_top_over_the_image():
    assert render.title_placement("crop", 1080, 1920, (1276, 720), TITLE, 88) == (
        8,
        int(1920 * 0.12),
    )


def test_facecam_title_sits_below_the_face_above_dialogue():
    alignment, margin = render.title_placement(
        "facecam", 1080, 1920, (1280, 720), TITLE, 88
    )
    assert alignment == 8  # top of gameplay, below the camera
    assert margin == 600 + int(1920 * 0.035)


def test_long_hook_is_reduced_to_fit_above_captions():
    import textwrap
    text = TITLE * 6
    size = render.fitted_title_size(1080, 1920, text, 88)
    assert size < 88
    columns = int(1080 * 0.86 / (size * 0.65))
    block = len(textwrap.wrap(text.upper(), width=columns)) * size * 1.25
    assert block <= 1920 * 0.20


def test_blur_without_a_known_source_size_still_places_the_title():
    assert render.title_placement("blur", 1080, 1920, None, TITLE, 88) == (
        8,
        int(1920 * 0.12),
    )


def test_a_source_with_no_band_falls_back_to_over_the_image():
    """A vertical source fills the frame; there is no band to centre in."""
    assert render.title_placement("blur", 1080, 1920, (1080, 1920), TITLE, 88) == (
        8,
        int(1920 * 0.12),
    )


# -- How ---------------------------------------------------------------


def read_ass(path):
    return path.read_text(encoding="utf-8").splitlines()


def test_title_gets_its_own_style_and_spans_the_whole_clip(tmp_path):
    out = tmp_path / "clip.ass"
    render.write_ass(
        [],
        str(out),
        1080,
        1920,
        title="El home run más limpio",
        title_size=88,
        title_alignment=8,
        title_margin_v=217,
        duration=26.0,
    )
    lines = read_ass(out)
    assert "WrapStyle: 0" in lines
    style = next(line for line in lines if line.startswith("Style: Title"))
    fields = style.split(",")
    assert fields[2] == "88"
    assert fields[-5] == "8"  # alignment
    assert fields[-2] == "217"  # margin_v
    events = [line for line in lines if line.startswith("Dialogue:")]
    assert events == ["Dialogue: 1,0:00:00.00,0:00:26.00,Title,,0,0,0,,EL HOME RUN MÁS LIMPIO"]


def test_title_and_subtitles_coexist_on_separate_layers(tmp_path):
    out = tmp_path / "clip.ass"
    render.write_ass(
        [Word(0.0, 0.5, "hola")], str(out), 1080, 1920, title="GOL", duration=5.0
    )
    events = [line for line in read_ass(out) if line.startswith("Dialogue:")]
    assert events[0].startswith("Dialogue: 1,") and ",Title," in events[0]
    assert events[1].startswith("Dialogue: 0,") and ",Clip," in events[1]


def test_title_override_codes_are_neutralised(tmp_path):
    """A title is user text; it must not be able to inject ASS tags."""
    out = tmp_path / "clip.ass"
    render.write_ass([], str(out), 1080, 1920, title="{\\pos(0,0)}hack\\N", duration=3.0)
    event = next(line for line in read_ass(out) if line.startswith("Dialogue:"))
    text = event.split(",,0,0,0,,", 1)[1]
    assert "{" not in text and "\\" not in text


def test_no_title_writes_no_title_event(tmp_path):
    out = tmp_path / "clip.ass"
    render.write_ass([Word(0.0, 0.5, "hola")], str(out), 1080, 1920)
    events = [line for line in read_ass(out) if line.startswith("Dialogue:")]
    assert len(events) == 1 and ",Clip," in events[0]
