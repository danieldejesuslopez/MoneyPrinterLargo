"""Find and follow a visible face in a clip without changing its video.

Detection is local: OpenCV ships the Haar classifiers, so no model or
source frames are uploaded. Only a small number of frames from the actual
clip are examined; ffmpeg still reads the original video for the panel.
"""

from __future__ import annotations

import importlib
import math
import os
from dataclasses import dataclass, replace


SAMPLES_PER_SECOND = 2
MAX_SAMPLES = 120
VISIBILITY_PADDING = 1.0
MAX_SCAN_SIDE = 1280

Box = tuple[float, float, float, float]


class FaceDetectionError(RuntimeError):
    """The scan could not run; this is different from finding no face."""


def _number(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".") or "0"


def _sum_expression(terms: list[str]) -> str:
    """Balance sums so ffmpeg's expression parser does not hit its depth limit."""
    if not terms:
        return "0"
    while len(terms) > 1:
        terms = [
            f"({terms[index]}+{terms[index + 1]})"
            if index + 1 < len(terms) else terms[index]
            for index in range(0, len(terms), 2)
        ]
    return terms[0]


@dataclass(frozen=True)
class FaceTrack:
    """A fixed-size crop with timed positions and visible intervals.

    Times are relative to the beginning of the clip, not the source.
    Expressions contain commas: quote them when placing them in an
    ffmpeg filter graph (for example ``x='expression'``).
    """

    crop_w: int
    crop_h: int
    points: tuple[tuple[float, int, int], ...]
    intervals: tuple[tuple[float, float], ...]

    def position_expression(self, axis: str) -> str:
        """Linear movement expressed as a balanced sum of bounded ramps."""
        if axis not in ("x", "y"):
            raise ValueError("axis must be x or y")
        if not self.points:
            return "0"
        column = 1 if axis == "x" else 2
        terms = [str(self.points[0][column])]
        for previous, current in zip(self.points, self.points[1:]):
            delta = current[column] - previous[column]
            seconds = current[0] - previous[0]
            if delta and seconds > 0:
                terms.append(
                    f"({delta})*clip((t-{_number(previous[0])})/"
                    f"{_number(seconds)},0,1)"
                )
        return _sum_expression(terms)

    def enable_expression(self) -> str:
        """Show the panel only near frames where the face was observed."""
        return _sum_expression([
            f"between(t,{_number(start)},{_number(end)})"
            for start, end in self.intervals
        ])


def _load_cv2():
    try:
        return importlib.import_module("cv2")
    except (ImportError, OSError) as exc:
        raise FaceDetectionError(
            "No se pudo cargar OpenCV para detectar caras. Instala las "
            "dependencias con: python -m pip install opencv-python-headless"
        ) from exc


def _classifiers(cv2) -> list[tuple[object, bool]]:
    directory = cv2.data.haarcascades
    frontal = cv2.CascadeClassifier(
        os.path.join(directory, "haarcascade_frontalface_default.xml")
    )
    if frontal.empty():
        raise FaceDetectionError(
            "OpenCV no incluye el detector de caras. Reinstala "
            "opencv-python-headless."
        )
    detectors = [(frontal, False)]
    profile = cv2.CascadeClassifier(os.path.join(directory, "haarcascade_profileface.xml"))
    if not profile.empty():
        detectors.extend(((profile, False), (profile, True)))
    return detectors


def _iou(left: Box, right: Box) -> float:
    x0, y0 = max(left[0], right[0]), max(left[1], right[1])
    x1 = min(left[0] + left[2], right[0] + right[2])
    y1 = min(left[1] + left[3], right[1] + right[3])
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    union = left[2] * left[3] + right[2] * right[3] - intersection
    return intersection / union if union else 0.0


def _detect_faces(cv2, detectors, frame, source_size: tuple[int, int]) -> list[Box]:
    frame_h, frame_w = frame.shape[:2]
    scale = min(1.0, MAX_SCAN_SIDE / max(frame_w, frame_h))
    if scale < 1:
        frame = cv2.resize(frame, (round(frame_w * scale), round(frame_h * scale)))
    gray = cv2.equalizeHist(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
    scan_h, scan_w = gray.shape[:2]
    found: list[Box] = []
    for detector, mirrored in detectors:
        image = cv2.flip(gray, 1) if mirrored else gray
        for x, y, width, height in detector.detectMultiScale(
            image, scaleFactor=1.1, minNeighbors=5, minSize=(20, 20)
        ):
            if mirrored:
                x = scan_w - x - width
            box = (
                float(x) * source_size[0] / scan_w,
                float(y) * source_size[1] / scan_h,
                float(width) * source_size[0] / scan_w,
                float(height) * source_size[1] / scan_h,
            )
            found.append(box)
    # Frontal/profile classifiers often find the same face. Keep one box
    # per face so duplicates cannot count as persistence across frames.
    unique: list[Box] = []
    for box in sorted(found, key=lambda b: b[2] * b[3], reverse=True):
        if not any(_iou(box, previous) > 0.35 for previous in unique):
            unique.append(box)
    return unique


def _sample_times(duration: float) -> tuple[float, ...]:
    count = min(MAX_SAMPLES, max(1, math.floor(duration * SAMPLES_PER_SECOND)))
    return tuple(index * duration / count for index in range(count))


def _match_cost(previous: Box, current: Box) -> float | None:
    size_ratio = max(current[2], current[3]) / max(previous[2], previous[3])
    if not 0.35 <= size_ratio <= 2.85:
        return None
    distance = math.hypot(
        current[0] + current[2] / 2 - previous[0] - previous[2] / 2,
        current[1] + current[3] / 2 - previous[1] - previous[3] / 2,
    )
    distance /= max(previous[2], previous[3], current[2], current[3])
    return distance if distance <= 2.5 else None


def _select_track(
    observations: list[tuple[float, list[Box]]],
    sample_count: int,
    padding: float = VISIBILITY_PADDING,
) -> list[tuple[float, Box]]:
    """Follow the persistent face, filling other scenes with visible faces.

    The main camera has priority wherever it was observed recently. Other
    persistent tracks can fill uncovered scenes, such as an original zoom
    that moves the camera from a corner to the centre of the source.
    """
    tracks: list[list[tuple[float, Box]]] = []
    spacing = (
        (observations[-1][0] - observations[0][0]) / (len(observations) - 1)
        if len(observations) > 1 else 0.5
    )
    max_gap = max(3.0, spacing * 2.5)
    for seconds, boxes in observations:
        pairs = []
        for track_index, track in enumerate(tracks):
            for box_index, box in enumerate(boxes):
                cost = _match_cost(track[-1][1], box)
                if cost is not None and seconds - track[-1][0] > max_gap:
                    # A streamer can turn away or leave the camera for a
                    # while. Rejoin a returning face only near its old
                    # position/scale; the panel remains hidden in the gap.
                    old_box = track[-1][1]
                    size_ratio = max(box[2], box[3]) / max(old_box[2], old_box[3])
                    if cost > 0.5 or not 0.67 <= size_ratio <= 1.5:
                        continue
                if cost is not None:
                    pairs.append((cost, track_index, box_index))
        used_tracks: set[int] = set()
        used_boxes: set[int] = set()
        for _, track_index, box_index in sorted(pairs):
            if track_index not in used_tracks and box_index not in used_boxes:
                tracks[track_index].append((seconds, boxes[box_index]))
                used_tracks.add(track_index)
                used_boxes.add(box_index)
        for box_index, box in enumerate(boxes):
            if box_index not in used_boxes:
                tracks.append([(seconds, box)])
    if not tracks:
        return []
    ranked = sorted(
        tracks,
        key=lambda track: (
            len(track),
            sum(box[2] * box[3] for _, box in track) / len(track),
        ),
        reverse=True,
    )
    selected: list[tuple[float, Box]] = []
    covered: list[tuple[float, float]] = []
    for track in ranked:
        if len(track) < min(2, sample_count):
            continue
        added = [
            (seconds, box) for seconds, box in track
            if not any(left <= seconds <= right for left, right in covered)
        ]
        selected.extend(added)
        # Add a whole track at once: the samples within one camera must
        # not suppress one another, only lower-priority cameras.
        covered.extend((seconds - padding, seconds + padding) for seconds, _ in added)
    return sorted(selected, key=lambda detection: detection[0])


def _even_floor(value: float) -> int:
    return max(2, int(value) // 2 * 2)


def _clipped_intervals(
    observations: list[tuple[float, list[Box]]],
    selected: list[tuple[float, Box]],
    visible_region: Box,
    duration: float,
) -> tuple[tuple[float, float], ...]:
    """Use a panel only where the main frame does not contain the face.

    Check the detected face itself, rather than the larger context crop
    used for its panel. Neighbouring samples delimit each time cell, so
    visibility padding never spills over a known, fully visible face.
    """
    left, top, width, height = visible_region
    right, bottom = left + width, top + height
    selected_by_time = dict(selected)
    intervals: list[tuple[float, float]] = []
    tolerance = 1.0  # Scaling/crop rounding can differ by a source pixel.
    for index, (seconds, _) in enumerate(observations):
        box = selected_by_time.get(seconds)
        if box is None:
            continue
        x, y, face_w, face_h = box
        if (
            x >= left - tolerance and y >= top - tolerance
            and x + face_w <= right + tolerance
            and y + face_h <= bottom + tolerance
        ):
            continue
        start = (observations[index - 1][0] + seconds) / 2 if index else 0.0
        end = (
            (seconds + observations[index + 1][0]) / 2
            if index + 1 < len(observations) else duration
        )
        start, end = max(0.0, start), min(duration, end)
        if end <= start:
            continue
        if intervals and start <= intervals[-1][1]:
            intervals[-1] = (intervals[-1][0], end)
        else:
            intervals.append((start, end))
    return tuple(intervals)


def _make_track(
    detections: list[tuple[float, Box]],
    source_size: tuple[int, int],
    duration: float,
    panel_aspect: float,
    padding: float = VISIBILITY_PADDING,
) -> FaceTrack:
    source_w, source_h = source_size
    # One crop size for the whole clip avoids introducing any zoom. Its
    # margin accommodates the largest observed face and keeps the head.
    needed_h = max(
        max(box[3] * 1.6, box[2] * 1.4 / panel_aspect)
        for _, box in detections
    )
    needed_h = math.ceil(needed_h / 2) * 2
    fit = min(1.0, source_w / (needed_h * panel_aspect), source_h / needed_h)
    crop_h = min(_even_floor(source_h), _even_floor(needed_h * fit))
    crop_w = min(_even_floor(source_w), _even_floor(crop_h * panel_aspect))
    max_x = int(source_w - crop_w) // 2 * 2
    max_y = int(source_h - crop_h) // 2 * 2
    points = []
    intervals: list[tuple[float, float]] = []
    previous_detection: tuple[float, Box] | None = None

    def append_point(seconds: float, x: int, y: int) -> None:
        if not points or seconds > points[-1][0]:
            points.append((seconds, x, y))

    for seconds, box in detections:
        x = min(max_x, max(0, round((box[0] + box[2] / 2 - crop_w / 2) / 2) * 2))
        y = min(max_y, max(0, round((box[1] + box[3] / 2 - crop_h / 2) / 2) * 2))
        seconds = min(duration, max(0.0, seconds))
        if previous_detection is not None:
            previous_time, previous_box = previous_detection
            if (
                _match_cost(previous_box, box) is None
                or seconds - previous_time > padding * 2
            ):
                # Hold the previous camera while it is visible. Move
                # through a hidden gap, or cut promptly between cameras,
                # rather than sweeping across unrelated source content.
                previous_end = min(seconds, previous_time + padding)
                next_start = max(previous_time, seconds - padding)
                previous_x, previous_y = points[-1][1:]
                if previous_end < next_start:
                    append_point(previous_end, previous_x, previous_y)
                    append_point(next_start, x, y)
                else:
                    append_point(max(previous_time, previous_end - 0.001), previous_x, previous_y)
                    append_point(previous_end, x, y)
        append_point(seconds, x, y)
        previous_detection = (seconds, box)
        left = max(0.0, seconds - padding)
        right = min(duration, seconds + padding)
        if intervals and left <= intervals[-1][1]:
            intervals[-1] = (intervals[-1][0], max(right, intervals[-1][1]))
        else:
            intervals.append((left, right))
    return FaceTrack(crop_w, crop_h, tuple(points), tuple(intervals))


def scan_faces(
    source_path: str,
    start: float,
    duration: float,
    source_size: tuple[int, int],
    panel_aspect: float = 1.8,
    visible_region: Box | None = None,
) -> FaceTrack | None:
    """Scan only this clip and return its most persistent visible face.

    At most two frames per second and 120 frames total are decoded, spread
    over the whole clip. A one-frame false positive is ignored unless the
    clip is too short to have a second sample. With `visible_region`, a
    panel is returned only for times where the main crop cuts the face.
    """
    if (
        not all(math.isfinite(value) for value in (start, duration, panel_aspect))
        or start < 0 or duration <= 0 or panel_aspect <= 0
        or min(source_size) < 2
    ):
        raise FaceDetectionError("El tramo o las dimensiones del vídeo no son válidos.")
    cv2 = _load_cv2()
    capture = None
    try:
        capture = cv2.VideoCapture(os.fspath(source_path))
        if not capture.isOpened():
            raise FaceDetectionError(f"No se pudo abrir el vídeo para detectar caras: {source_path}")
        detectors = _classifiers(cv2)
        samples = _sample_times(duration)
        observations: list[tuple[float, list[Box]]] = []
        last_sample_time = None
        for relative_time in samples:
            last_sample_time = None
            requested = start + relative_time
            if not capture.set(cv2.CAP_PROP_POS_MSEC, requested * 1000):
                raise FaceDetectionError(f"No se pudo buscar el segundo {requested:.2f} del vídeo.")
            ok, frame = capture.read()
            if not ok or frame is None:
                raise FaceDetectionError(f"No se pudo decodificar el vídeo en el segundo {requested:.2f}.")
            # OpenCV reports the decoded frame timestamp. Rounding when
            # seeking can put it just before the requested clip boundary.
            decoded = float(capture.get(cv2.CAP_PROP_POS_MSEC)) / 1000
            if not math.isfinite(decoded) or decoded < start - 0.1:
                raise FaceDetectionError("El decodificador no devolvió tiempos válidos para detectar caras.")
            if decoded >= start + duration:
                continue
            seconds = max(0.0, decoded - start)
            if observations and seconds <= observations[-1][0] + 0.000001:
                # Very low frame rates can seek repeatedly to one frame;
                # that is still only one observation of a possible face.
                continue
            boxes = _detect_faces(cv2, detectors, frame, source_size)
            observations.append((seconds, boxes))
            last_sample_time = seconds
        # Long manual cuts spread the sampling budget farther apart.
        # Cover half the sample spacing to avoid artificial panel flicker
        # between successive detections of a continuously visible face.
        padding = max(VISIBILITY_PADDING, duration / len(samples) / 2)
        selected = _select_track(observations, len(samples), padding)
        if not selected:
            return None
        if visible_region is not None:
            intervals = _clipped_intervals(observations, selected, visible_region, duration)
            if not intervals:
                return None
            track = _make_track(selected, source_size, duration, panel_aspect, padding)
            return replace(track, intervals=intervals)
        track = _make_track(selected, source_size, duration, panel_aspect, padding)
        if last_sample_time is not None and selected[-1][0] == last_sample_time:
            # The sampling budget may leave half a sample spacing at the
            # end of a long cut. A face confirmed in the final sample can
            # remain visible through that tail, without extending an old
            # face when the final samples showed none.
            track = replace(
                track,
                intervals=track.intervals[:-1] + ((track.intervals[-1][0], duration),),
            )
        return track
    except FaceDetectionError:
        raise
    except Exception as exc:
        raise FaceDetectionError(f"No se pudo analizar el vídeo para detectar caras: {exc}") from exc
    finally:
        if capture is not None:
            capture.release()
