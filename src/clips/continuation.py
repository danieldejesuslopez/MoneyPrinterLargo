"""Consecutive clip boundaries and coverage of a whole source video.

Planning is independent of rendering: the same boundaries can be shown
before a run and revisited after an interruption. Existing clips count
only while their local file remains available and they were not rejected.
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from pathlib import Path
from typing import Any, Iterable, Sequence

from .segments import Word, group_sentences


BOUNDARY_WINDOW = 2.0
TIME_TOLERANCE = 0.000001


def _finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _timed_words(words: Sequence[Word], duration: float) -> list[Word]:
    usable = []
    for word in words or ():
        start = _finite_number(getattr(word, "start", None))
        end = _finite_number(getattr(word, "end", None))
        if start is None or end is None or start < 0 or end <= start or start >= duration:
            continue
        usable.append(Word(start, min(duration, end), str(getattr(word, "text", "") or "")))
    return sorted(usable, key=lambda word: (word.start, word.end))


def _safe_boundaries(candidates: Iterable[float], words: Sequence[Word]) -> list[float]:
    """A word ending is not safe if another overlapping word still speaks."""
    safe = []
    index = 0
    latest_end = 0.0
    for candidate in sorted(set(candidates)):
        while index < len(words) and words[index].start < candidate:
            latest_end = max(latest_end, words[index].end)
            index += 1
        if latest_end <= candidate:
            safe.append(candidate)
    return safe


def plan_sequence(
    duration: float,
    clip_seconds: float = 60,
    words: Sequence[Word] = (),
) -> list[tuple[float, float]]:
    """Cover ``0..duration`` exactly with stable, consecutive clip spans.

    ``clip_seconds`` is a target rather than a hard boundary: when timed
    speech is available, a nearby sentence ending (then a word ending) is
    preferred within two seconds. Silence and a short final remainder are
    retained, so no section of the source disappears between parts.
    """
    duration = _finite_number(duration)
    clip_seconds = _finite_number(clip_seconds)
    if duration is None or duration <= 0:
        raise ValueError("La duración del video debe ser un número positivo y finito.")
    if clip_seconds is None or not 5 <= clip_seconds <= 180:
        raise ValueError("La duración de cada parte debe estar entre 5 y 180 segundos.")

    timed = _timed_words(words, duration)
    sentence_ends = _safe_boundaries((sentence.end for sentence in group_sentences(timed)), timed)
    word_ends = _safe_boundaries((word.end for word in timed), timed)
    spans = []
    start = 0.0
    while start < duration:
        target = min(duration, start + clip_seconds)
        end = target
        if target < duration:
            low = max(start + clip_seconds / 2, target - BOUNDARY_WINDOW)
            high = min(duration, target + BOUNDARY_WINDOW, start + 180)
            for candidates in (sentence_ends, word_ends):
                nearby = candidates[bisect_left(candidates, low):bisect_right(candidates, high)]
                if nearby:
                    end = min(nearby, key=lambda candidate: (abs(candidate - target), candidate))
                    break
        if end <= start:
            raise ValueError("La precisión de los tiempos no permite dividir este video.")
        spans.append((start, end))
        start = end
    return spans


def _valid_record(record: Any, source_id: str) -> tuple[float, float, str] | None:
    if not isinstance(record, dict) or record.get("source_id") != source_id:
        return None
    if record.get("status") == "rejected":
        return None
    start = _finite_number(record.get("start"))
    end = _finite_number(record.get("end"))
    if start is None or end is None or start < 0 or end <= start:
        return None
    try:
        path = record.get("path")
        if not path or not Path(path).is_file():
            return None
    except (TypeError, ValueError, OSError):
        return None
    return start, end, str(record.get("id") or "")


def coverage_plan(
    spans: Sequence[tuple[float, float]],
    records: Iterable[dict[str, Any]],
    source_id: str,
) -> dict[str, Any]:
    """Which planned parts are already represented by usable local clips.

    Coverage uses the union of existing intervals. A later clip never
    hides an earlier hole, and an unavailable or rejected clip can be
    created again. A microsecond tolerance accommodates catalogue times
    stored with six decimal places without treating meaningful gaps as
    covered.
    """
    existing = sorted(
        (interval for record in records if (interval := _valid_record(record, source_id)) is not None),
        key=lambda interval: (interval[0], interval[1], interval[2]),
    )
    parts: list[dict[str, Any]] = []
    for index, span in enumerate(spans, start=1):
        try:
            start, end = (_finite_number(value) for value in span)
        except (TypeError, ValueError):
            raise ValueError("Cada parte debe contener un inicio y un final válidos.") from None
        if start is None or end is None or start < 0 or end <= start:
            raise ValueError("Cada parte debe tener tiempos positivos, finitos y ordenados.")
        cursor = start
        contributors: list[str] = []
        for covered_start, covered_end, clip_id in existing:
            if covered_end <= cursor:
                continue
            if covered_start > cursor + TIME_TOLERANCE:
                break
            cursor = max(cursor, covered_end)
            contributors.append(clip_id)
            if cursor + TIME_TOLERANCE >= end:
                break
        covered = bool(contributors) and cursor + TIME_TOLERANCE >= end
        part: dict[str, Any] = {"index": index, "start": start, "end": end, "covered": covered}
        if covered and contributors[0]:
            part["clip_id"] = contributors[0]
        parts.append(part)
    remaining = [part for part in parts if not part["covered"]]
    return {
        "parts": parts,
        "total_parts": len(parts),
        "completed_parts": len(parts) - len(remaining),
        "remaining_parts": len(remaining),
        "next_start": remaining[0]["start"] if remaining else (parts[-1]["end"] if parts else 0.0),
        "complete": not remaining,
    }
