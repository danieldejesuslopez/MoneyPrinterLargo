"""A whole video remains covered when clipping resumes after a gap."""

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clips import continuation  # noqa: E402
from clips.segments import Word  # noqa: E402


def test_without_speech_every_second_and_the_short_final_part_are_kept():
    assert continuation.plan_sequence(125, 60) == [(0, 60), (60, 120), (120, 125)]
    assert continuation.plan_sequence(2, 60) == [(0, 2)]


def test_decimal_times_share_exact_boundaries_and_reach_the_actual_end():
    duration = 120.123456789
    spans = continuation.plan_sequence(duration, 7.5)
    assert spans[0][0] == 0
    assert spans[-1][1] == duration
    assert all(left[1] == right[0] for left, right in zip(spans, spans[1:]))
    assert sum(end - start for start, end in spans) == duration


def test_a_nearby_sentence_ending_is_preferred_to_cutting_into_the_next_sentence():
    words = [
        Word(57, 58, "Hola"), Word(58, 59.1, "mundo."),
        Word(59.2, 59.8, "Sigue"), Word(59.9, 60.4, "hablando"),
        Word(60.5, 61.2, "ahora."),
    ]
    spans = continuation.plan_sequence(125, 60, words)
    assert spans[0] == (0, 59.1)
    assert spans[1][0] == 59.1
    assert spans[-1][1] == 125


def test_word_boundaries_are_used_when_no_sentence_ends_near_the_target():
    words = [Word(58, 59.5, "Sigue"), Word(59.5, 60.2, "hablando"), Word(60.2, 63, "todavía")]
    assert continuation.plan_sequence(125, 60, words)[0] == (0, 60.2)


def test_an_overlapping_word_ending_is_not_a_safe_cut():
    words = [Word(58, 59.8, "una"), Word(59.7, 60.5, "otra"), Word(60.5, 64, "larga")]
    assert continuation.plan_sequence(125, 60, words)[0] == (0, 60.5)


def test_invalid_word_timings_do_not_corrupt_the_plan():
    words = [Word(math.nan, 60, "no"), Word(1, math.inf, "no"), Word(-1, 60, "no")]
    assert continuation.plan_sequence(125, 60, words) == continuation.plan_sequence(125, 60)


@pytest.mark.parametrize("duration", [0, -1, math.inf, math.nan, "bad", None])
def test_invalid_source_duration_is_rejected(duration):
    with pytest.raises(ValueError):
        continuation.plan_sequence(duration)


@pytest.mark.parametrize("seconds", [0, 4.9, 180.1, math.inf, math.nan, "bad", None])
def test_invalid_part_duration_is_rejected(seconds):
    with pytest.raises(ValueError):
        continuation.plan_sequence(125, seconds)


def record(tmp_path, clip_id, start, end, **changes):
    path = tmp_path / f"{clip_id}.mp4"
    path.write_bytes(b"rendered clip")
    return {"id": clip_id, "source_id": "source", "path": str(path), "start": start, "end": end, **changes}


def test_continuation_finds_an_earlier_hole_instead_of_using_the_furthest_end(tmp_path):
    rows = [record(tmp_path, "first", 0, 60), record(tmp_path, "third", 120, 125)]
    result = continuation.coverage_plan(continuation.plan_sequence(125), rows, "source")
    assert [part["covered"] for part in result["parts"]] == [True, False, True]
    assert [part["index"] for part in result["parts"]] == [1, 2, 3]
    assert result["next_start"] == 60
    assert result["completed_parts"] == 2
    assert result["remaining_parts"] == 1
    assert result["complete"] is False


def test_multiple_adjacent_existing_clips_can_cover_one_planned_part(tmp_path):
    rows = [record(tmp_path, "left", 0, 30), record(tmp_path, "right", 30, 60)]
    result = continuation.coverage_plan([(0, 60)], rows, "source")
    assert result["complete"] is True
    assert result["completed_parts"] == result["total_parts"] == 1
    assert result["next_start"] == 60


def test_a_gap_inside_a_part_is_not_hidden_by_a_later_clip(tmp_path):
    rows = [record(tmp_path, "left", 0, 20), record(tmp_path, "right", 40, 60)]
    result = continuation.coverage_plan([(0, 60)], rows, "source")
    assert result["complete"] is False
    assert result["next_start"] == 0


def test_rejected_missing_other_source_and_invalid_records_never_count_as_coverage(tmp_path):
    missing = record(tmp_path, "missing", 0, 60)
    Path(missing["path"]).unlink()
    rows = [
        missing, record(tmp_path, "rejected", 0, 60, status="rejected"),
        record(tmp_path, "other", 0, 60, source_id="other"),
        record(tmp_path, "nan", math.nan, 60), record(tmp_path, "inf", 0, math.inf),
        record(tmp_path, "reverse", 60, 0), record(tmp_path, "negative", -1, 60),
        {"source_id": "source", "start": 0, "end": 60, "path": "\0invalid"}, None,
    ]
    result = continuation.coverage_plan([(0, 60)], rows, "source")
    assert result["completed_parts"] == 0
    assert result["remaining_parts"] == 1


def test_six_decimal_catalogue_rounding_does_not_make_a_fake_gap(tmp_path):
    spans = [(0, 60.1234567), (60.1234567, 125)]
    rows = [record(tmp_path, "one", 0, 60.123457), record(tmp_path, "two", 60.123457, 125)]
    assert continuation.coverage_plan(spans, rows, "source")["complete"] is True


def test_a_missing_tiny_final_part_is_not_claimed_as_covered(tmp_path):
    rows = [record(tmp_path, "first", 0, 60)]
    result = continuation.coverage_plan([(60, 60.0000001)], rows, "source")
    assert result["complete"] is False


@pytest.mark.parametrize("spans", [[(0, math.nan)], [(0, math.inf)], [(5, 4)], [(-1, 5)], [(0,)], [None]])
def test_invalid_planned_spans_are_rejected(spans):
    with pytest.raises(ValueError):
        continuation.coverage_plan(spans, [], "source")


def test_an_empty_plan_has_no_remaining_work():
    assert continuation.coverage_plan([], [], "source") == {
        "parts": [], "total_parts": 0, "completed_parts": 0,
        "remaining_parts": 0, "next_start": 0, "complete": True,
    }
