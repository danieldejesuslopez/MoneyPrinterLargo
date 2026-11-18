"""Face selection, movement and clip-limited decoding without a real camera."""

import math
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clips import faces  # noqa: E402


def test_persistent_face_wins_over_a_larger_one_frame_face():
    small = (100, 100, 40, 40)
    large = (700, 100, 150, 150)
    selected = faces._select_track(
        [(0, [small]), (0.5, [small, large]), (1, [small])], 3
    )
    assert [box for _, box in selected] == [small, small, small]


def test_original_zoom_and_distant_corner_camera_both_keep_a_face_track():
    large = (700, 200, 350, 350)
    corner = (80, 30, 70, 70)
    selected = faces._select_track(
        [(0, [large]), (0.5, [large]), (1, [large]),
         (1.5, [corner]), (2, [corner]), (2.5, [corner]), (3, [corner])],
        7,
    )
    assert any(box == large for _, box in selected)
    assert any(box == corner for _, box in selected)
    assert len({seconds for seconds, _ in selected}) == len(selected)


def test_persistent_secondary_face_never_replaces_the_simultaneous_main_face():
    main = (100, 100, 40, 40)
    secondary = (700, 100, 150, 150)
    selected = faces._select_track(
        [(0, [main]), (0.5, [main, secondary]), (1, [main, secondary]), (1.5, [main])],
        4,
    )
    assert [box for _, box in selected] == [main] * 4


def test_an_isolated_detection_does_not_create_a_face_panel():
    box = (100, 100, 40, 40)
    assert faces._select_track([(0, []), (0.5, [box]), (1, [])], 3) == []
    assert faces._select_track([(0, [box])], 1) == [(0, box)]


def test_distant_detections_are_not_combined_into_one_moving_face():
    selected = faces._select_track(
        [(0, [(0, 0, 40, 40)]), (0.5, [(900, 500, 40, 40)])], 2
    )
    assert selected == []


def test_face_returning_after_five_seconds_rejoins_its_camera_track():
    first = (100, 100, 40, 40)
    returned = (110, 100, 42, 42)
    selected = faces._select_track(
        [(0, [first]), (0.5, [first])] + [(time, []) for time in (1, 2, 3, 4, 5)]
        + [(6, [returned]), (6.5, [returned])],
        9,
    )
    assert [seconds for seconds, _ in selected] == [0, 0.5, 6, 6.5]
    track = faces._make_track(selected, (1280, 720), 8, 1.8)
    assert track.intervals == ((0, 1.5), (5, 7.5))


def test_a_different_position_after_a_long_gap_starts_a_separate_track():
    first = (100, 100, 40, 40)
    different = (170, 100, 40, 40)
    selected = faces._select_track(
        [(0, [first]), (0.5, [first]), (1, []), (2, []), (3, []), (4, []), (5, []), (6, [different])],
        8,
    )
    assert [seconds for seconds, _ in selected] == [0, 0.5]


def test_two_simultaneous_faces_are_not_counted_as_two_frames():
    assert faces._select_track([(0, [(0, 0, 40, 40), (300, 0, 40, 40)])], 2) == []


def test_track_follows_movement_with_one_fixed_crop_size():
    track = faces._make_track(
        [(0, (100, 100, 60, 60)), (0.5, (140, 120, 80, 80)), (1, (180, 140, 70, 70))],
        (1280, 720), 2, 1.8,
    )
    assert track.crop_h >= 80 * 1.6
    assert track.crop_w / track.crop_h == pytest.approx(1.8, abs=0.02)
    assert track.points[0][1] < track.points[-1][1]
    assert track.points[0][2] < track.points[-1][2]
    assert "clip((t-0)/0.5,0,1)" in track.position_expression("x")


@pytest.mark.parametrize("source_size", [(1280, 720), (101, 61), (60, 101)])
def test_all_crops_stay_even_and_inside_the_source(source_size):
    width, height = source_size
    track = faces._make_track(
        [(0, (0, 0, 30, 30)), (1, (width - 30, height - 30, 30, 30))],
        source_size, 2, 1.8,
    )
    assert track.crop_w % 2 == track.crop_h % 2 == 0
    for _, x, y in track.points:
        assert x % 2 == y % 2 == 0
        assert 0 <= x <= width - track.crop_w
        assert 0 <= y <= height - track.crop_h


def _evaluate(expression, seconds):
    return eval(expression, {"__builtins__": {}}, {
        "t": seconds,
        "clip": lambda value, low, high: max(low, min(high, value)),
        "between": lambda value, low, high: int(low <= value <= high),
    })


def test_ramp_expression_interpolates_forward_backward_and_holds_at_edges():
    track = faces.FaceTrack(100, 60, ((1, 10, 20), (2, 30, 40), (4, 20, 10)), ())
    x = track.position_expression("x")
    assert [_evaluate(x, time) for time in (0, 1, 1.5, 2, 3, 4, 10)] == [10, 10, 20, 30, 25, 20, 20]
    y = track.position_expression("y")
    assert _evaluate(y, 3) == 25
    with pytest.raises(ValueError):
        track.position_expression("width")


def test_panel_disappears_during_a_gap_without_a_visible_face():
    track = faces._make_track(
        [(0.5, (100, 100, 40, 40)), (1, (100, 100, 40, 40)), (5, (100, 100, 40, 40))],
        (1280, 720), 6, 1.8,
    )
    assert track.intervals == ((0, 2), (4, 6))
    assert _evaluate(track.enable_expression(), 1) == 1
    assert _evaluate(track.enable_expression(), 3) == 0
    assert _evaluate(track.enable_expression(), 5) == 1


def test_long_clip_padding_does_not_blink_between_sparse_continuous_detections():
    box = (100, 100, 40, 40)
    track = faces._make_track([(0, box), (5, box), (10, box)], (1280, 720), 12, 1.8, padding=2.5)
    assert track.intervals == ((0, 12),)


def test_scene_change_holds_camera_until_its_visibility_ends():
    track = faces._make_track(
        [(0, (100, 100, 40, 40)), (0.5, (100, 100, 40, 40)),
         (2, (700, 300, 40, 40)), (2.5, (700, 300, 40, 40))],
        (1280, 720), 4, 1.8,
    )
    expression = track.position_expression("x")
    assert _evaluate(expression, 1) == track.points[0][1]
    assert _evaluate(expression, 1.6) == track.points[-1][1]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_ffmpeg_can_evaluate_120_moving_crop_points():
    track = faces.FaceTrack(
        160, 90,
        tuple((index / 30, (index * 13) % 160, (index * 7) % 90) for index in range(120)),
        tuple((index / 30, index / 30 + 0.01) for index in range(120)),
    )
    graph = (
        f"crop=160:90:x='{track.position_expression('x')}':y='{track.position_expression('y')}',"
        f"drawbox=x=0:y=0:w=10:h=10:enable='{track.enable_expression()}'"
    )
    result = subprocess.run(
        [shutil.which("ffmpeg"), "-v", "error", "-f", "lavfi", "-i", "color=s=320x180:r=30:d=0.1",
         "-vf", graph, "-frames:v", "2", "-f", "null", "-"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_samples_cover_long_clips_without_exceeding_the_budget():
    samples = faces._sample_times(180)
    assert len(samples) == 120
    assert samples[0] == 0
    assert samples[-1] >= 178
    assert all(right - left >= 0.5 for left, right in zip(samples, samples[1:]))
    assert faces._sample_times(0.1) == (0,)
    assert len(faces._sample_times(1.2)) / 1.2 <= 2
    assert all(
        right - left >= 0.5
        for left, right in zip(faces._sample_times(1.2), faces._sample_times(1.2)[1:])
    )


class FakeCapture:
    def __init__(self, *, fail_at=None, opened=True, timestamp_offset=0):
        self.requested = []
        self.released = False
        self.fail_at = fail_at
        self.opened = opened
        self.timestamp_offset = timestamp_offset

    def isOpened(self):
        return self.opened

    def set(self, _, milliseconds):
        self.requested.append(milliseconds)
        return True

    def read(self):
        return (False, None) if len(self.requested) == self.fail_at else (True, object())

    def get(self, _):
        return self.requested[-1] + self.timestamp_offset

    def release(self):
        self.released = True


def _fake_scan(monkeypatch, capture, boxes):
    fake_cv2 = SimpleNamespace(VideoCapture=lambda _: capture, CAP_PROP_POS_MSEC=0)
    monkeypatch.setattr(faces, "_load_cv2", lambda: fake_cv2)
    monkeypatch.setattr(faces, "_classifiers", lambda _: [])
    monkeypatch.setattr(faces, "_detect_faces", lambda *_: boxes)


def test_scan_seeks_only_inside_the_clip_and_uses_decoded_timestamps(monkeypatch):
    capture = FakeCapture(timestamp_offset=40)
    _fake_scan(monkeypatch, capture, [(100, 100, 60, 60)])
    track = faces.scan_faces("source.mp4", 12, 2, (1280, 720))
    assert capture.requested == [12000, 12500, 13000, 13500]
    assert [point[0] for point in track.points] == pytest.approx([0.04, 0.54, 1.04, 1.54])
    assert capture.released


def test_scan_with_no_faces_returns_none_and_releases_video(monkeypatch):
    capture = FakeCapture()
    _fake_scan(monkeypatch, capture, [])
    assert faces.scan_faces("source.mp4", 0, 2, (1280, 720)) is None
    assert capture.released


@pytest.mark.parametrize(
    "box",
    [
        (500, 200, 60, 60),  # Fully inside the final crop.
        (400, 100, 60, 60),  # Touching its top-left edge is still complete.
        (740, 440, 60, 60),  # Touching its bottom-right edge.
        (399.25, 99.25, 60, 60),  # Subpixel scale rounding, within tolerance.
        (740.75, 440.75, 60, 60),
    ],
)
def test_a_complete_face_in_the_main_crop_does_not_get_a_duplicate_panel(monkeypatch, box):
    capture = FakeCapture()
    _fake_scan(monkeypatch, capture, [box])
    assert faces.scan_faces(
        "source.mp4", 0, 2, (1280, 720), visible_region=(400, 100, 400, 400)
    ) is None
    assert capture.released


@pytest.mark.parametrize(
    "box",
    [
        (390, 200, 60, 60),  # Partially clipped on each of the four edges.
        (750, 200, 60, 60),
        (500, 90, 60, 60),
        (500, 450, 60, 60),
        (100, 200, 60, 60),  # Entirely outside the final crop.
        (398.75, 200, 60, 60),  # More than one source pixel is truly clipped.
    ],
)
def test_a_clipped_or_missing_face_in_the_main_crop_gets_a_panel(monkeypatch, box):
    capture = FakeCapture()
    _fake_scan(monkeypatch, capture, [box])
    track = faces.scan_faces(
        "source.mp4", 0, 2, (1280, 720), visible_region=(400, 100, 400, 400)
    )
    assert track is not None
    assert track.intervals == ((0, 2),)
    assert capture.released


def test_panel_is_hidden_when_a_moving_face_becomes_complete_again(monkeypatch):
    capture = FakeCapture()
    clipped = (390, 200, 60, 60)
    complete = (410, 200, 60, 60)
    boxes = [clipped, complete, complete, clipped]
    _fake_scan(monkeypatch, capture, [])
    monkeypatch.setattr(
        faces, "_detect_faces", lambda *_: [boxes[len(capture.requested) - 1]]
    )
    track = faces.scan_faces(
        "source.mp4", 10, 2, (1280, 720), visible_region=(400, 100, 400, 400)
    )
    # Cells stop at neighboring sample midpoints; the old one-second
    # visibility padding must not leak a duplicate over complete faces.
    assert track.intervals == ((0, 0.25), (1.25, 2))
    assert [point[0] for point in track.points] == [0, 0.5, 1, 1.5]
    assert _evaluate(track.enable_expression(), 0.1) == 1
    assert _evaluate(track.enable_expression(), 0.5) == 0
    assert _evaluate(track.enable_expression(), 1) == 0
    assert _evaluate(track.enable_expression(), 1.5) == 1


def test_final_complete_face_does_not_extend_an_earlier_clipped_panel(monkeypatch):
    capture = FakeCapture()
    _fake_scan(monkeypatch, capture, [])
    monkeypatch.setattr(
        faces, "_detect_faces",
        lambda *_: [(390, 200, 60, 60)] if len(capture.requested) < 3 else [(410, 200, 60, 60)],
    )
    track = faces.scan_faces(
        "source.mp4", 0, 2, (1280, 720), visible_region=(400, 100, 400, 400)
    )
    assert track.intervals == ((0, 0.75),)
    assert _evaluate(track.enable_expression(), 1.75) == 0


def test_a_secondary_clipped_face_does_not_replace_the_complete_main_face(monkeypatch):
    capture = FakeCapture()
    main = (500, 200, 60, 60)
    secondary = (100, 200, 100, 100)
    _fake_scan(monkeypatch, capture, [])
    monkeypatch.setattr(
        faces, "_detect_faces",
        lambda *_: [main, secondary] if len(capture.requested) in (2, 3) else [main],
    )
    assert faces.scan_faces(
        "source.mp4", 0, 2, (1280, 720), visible_region=(400, 100, 400, 400)
    ) is None


def test_face_seen_in_final_long_clip_sample_remains_visible_through_the_end(monkeypatch):
    capture = FakeCapture()
    _fake_scan(monkeypatch, capture, [(100, 100, 60, 60)])
    track = faces.scan_faces("source.mp4", 0, 300, (1280, 720))
    assert len(capture.requested) == 120
    assert track.intervals == ((0, 300),)


def test_face_missing_from_final_samples_is_not_extended_to_the_end(monkeypatch):
    capture = FakeCapture()
    _fake_scan(monkeypatch, capture, [(100, 100, 60, 60)])
    monkeypatch.setattr(
        faces, "_detect_faces",
        lambda *_: [(100, 100, 60, 60)] if len(capture.requested) < 119 else [],
    )
    track = faces.scan_faces("source.mp4", 0, 300, (1280, 720))
    assert track.intervals == ((0, 293.75),)
    assert _evaluate(track.enable_expression(), 299) == 0


def test_repeated_seeks_to_one_frame_do_not_count_as_a_persistent_face(monkeypatch):
    capture = FakeCapture()
    capture.get = lambda _: 0
    _fake_scan(monkeypatch, capture, [(100, 100, 60, 60)])
    assert faces.scan_faces("source.mp4", 0, 2, (1280, 720)) is None
    assert capture.released


@pytest.mark.parametrize("capture", [FakeCapture(fail_at=2), FakeCapture(opened=False)])
def test_scan_reports_decode_errors_and_always_releases_video(monkeypatch, capture):
    _fake_scan(monkeypatch, capture, [])
    with pytest.raises(faces.FaceDetectionError, match="No se pudo"):
        faces.scan_faces("source.mp4", 0, 2, (1280, 720))
    assert capture.released


def test_missing_opencv_has_an_actionable_error(monkeypatch):
    def missing(_):
        raise ModuleNotFoundError("cv2")

    monkeypatch.setattr(faces.importlib, "import_module", missing)
    with pytest.raises(faces.FaceDetectionError, match="pip install opencv-python-headless"):
        faces.scan_faces("source.mp4", 0, 2, (1280, 720))


@pytest.mark.parametrize("duration", [0, -1, math.inf, math.nan])
def test_invalid_clip_length_fails_before_loading_opencv(monkeypatch, duration):
    monkeypatch.setattr(faces, "_load_cv2", lambda: pytest.fail("should not import"))
    with pytest.raises(faces.FaceDetectionError):
        faces.scan_faces("source.mp4", 0, duration, (1280, 720))
