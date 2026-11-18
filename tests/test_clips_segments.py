"""The clip module's pure logic: boundaries, candidates, selection, trends.

Nothing here touches ffmpeg, yt-dlp, Whisper or an LLM. These are the
decisions that determine whether a clip is watchable — where it cuts and
which one gets kept — so they are tested on fixtures instead of on a
render.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clips import segments, trends  # noqa: E402
from clips.segments import Sentence, Window, Word  # noqa: E402


def words(spec, start=0.0, step=0.4, gap_before=None):
    """Timed words from a list of strings, one every `step` seconds."""
    out = []
    clock = start
    for index, text in enumerate(spec):
        if gap_before and index in gap_before:
            clock += gap_before[index]
        out.append({"start": clock, "end": clock + step, "word": text})
        clock += step
    return out


def sentences(spans):
    """Sentences from (start, end) pairs, with plausible word counts."""
    return [
        Sentence(
            start=start,
            end=end,
            text=f"s{index}",
            words=[Word(start, end, f"w{index}")] * 3,
        )
        for index, (start, end) in enumerate(spans)
    ]


# -- Boundaries --------------------------------------------------------


def test_sentences_split_on_punctuation():
    parsed = segments.parse_words(words(["Hola", "mundo.", "Otra", "frase", "aqui."]))
    result = segments.group_sentences(parsed)
    assert len(result) == 2
    assert result[0].text == "Hola mundo."
    assert result[1].text == "Otra frase aqui."


def test_sentences_split_on_silence_without_punctuation():
    """Whisper omits punctuation on some models; a pause still ends a line."""
    parsed = segments.parse_words(
        words(["uno", "dos", "tres", "cuatro"], gap_before={2: 1.2})
    )
    result = segments.group_sentences(parsed, silence_gap=0.65)
    assert len(result) == 2
    assert result[0].text == "uno dos"
    assert result[1].text == "tres cuatro"


def test_one_word_fragment_is_merged_not_left_dangling():
    parsed = segments.parse_words(words(["uno", "dos", "tres"], gap_before={2: 1.2}))
    result = segments.group_sentences(parsed, silence_gap=0.65)
    assert len(result) == 1
    assert result[0].text == "uno dos tres"


def test_unpunctuated_monologue_still_gets_cut():
    parsed = segments.parse_words(words([f"w{i}" for i in range(95)]))
    result = segments.group_sentences(parsed)
    assert len(result) >= 2
    assert all(len(s.words) <= 40 for s in result)


def test_words_are_sorted_and_empty_tokens_dropped():
    parsed = segments.parse_words(
        [
            {"start": 5.0, "end": 5.4, "word": "later"},
            {"start": 1.0, "end": 1.4, "word": "  "},
            {"start": 0.0, "end": 0.4, "word": "first"},
            {"start": 2.0, "end": 2.0, "word": "zero-length"},
        ]
    )
    assert [w.text for w in parsed] == ["first", "later"]


# -- Candidates --------------------------------------------------------


def test_windows_never_cut_inside_a_sentence():
    spans = sentences([(i * 5.0, i * 5.0 + 5.0) for i in range(12)])
    built = segments.build_windows(spans, min_seconds=18.0, max_seconds=30.0)
    assert built
    starts = {s.start for s in spans}
    ends = {s.end for s in spans}
    for window in built:
        assert window.start in starts
        assert window.end in ends


def test_windows_respect_the_length_bounds():
    spans = sentences([(i * 4.0, i * 4.0 + 4.0) for i in range(20)])
    built = segments.build_windows(spans, min_seconds=20.0, max_seconds=35.0)
    assert built
    for window in built:
        assert 20.0 <= window.duration <= 35.0


def test_no_window_when_the_source_is_too_short():
    spans = sentences([(0.0, 3.0), (3.0, 6.0)])
    assert segments.build_windows(spans, min_seconds=18.0, max_seconds=60.0) == []


def test_window_takes_the_longest_run_that_fits():
    spans = sentences([(i * 5.0, i * 5.0 + 5.0) for i in range(10)])
    built = segments.build_windows(spans, min_seconds=10.0, max_seconds=22.0)
    # 4 sentences = 20s fits under 22s; a 5th would be 25s and must not.
    assert built[0].last_sentence == 3
    assert built[0].duration == pytest.approx(20.0)


def test_inverted_question_mark_does_not_end_a_sentence():
    """Spanish opens questions with a mark that must not be a boundary."""
    parsed = segments.parse_words(
        [
            {"start": 0.0, "end": 0.4, "word": "¿Que"},
            {"start": 0.4, "end": 0.8, "word": "pasa"},
            {"start": 0.8, "end": 1.2, "word": "aqui?"},
        ]
    )
    result = segments.group_sentences(parsed)
    assert len(result) == 1


# -- Overlap and selection ---------------------------------------------


def window(start, end, score=0.0):
    return Window(
        start=start, end=end, text="", first_sentence=0, last_sentence=0, score=score
    )


def test_overlap_is_measured_against_the_shorter_window():
    big = window(0.0, 60.0)
    inside = window(10.0, 30.0)
    assert segments.overlap_ratio(big, inside) == pytest.approx(1.0)


def test_disjoint_windows_do_not_overlap():
    assert segments.overlap_ratio(window(0.0, 20.0), window(20.0, 40.0)) == 0.0


def test_selection_drops_near_duplicates_of_a_better_clip():
    candidates = [
        window(0.0, 30.0, score=0.9),
        window(2.0, 32.0, score=0.8),  # almost the same moment
        window(60.0, 90.0, score=0.7),
    ]
    kept = segments.select_clips(candidates, count=3, max_overlap=0.25)
    assert [w.start for w in kept] == [0.0, 60.0]


def test_selection_returns_clips_in_source_order_not_score_order():
    candidates = [
        window(100.0, 130.0, score=0.9),
        window(0.0, 30.0, score=0.5),
    ]
    kept = segments.select_clips(candidates, count=2, max_overlap=0.25)
    assert [w.start for w in kept] == [0.0, 100.0]


def test_selection_honours_the_count():
    candidates = [window(i * 40.0, i * 40.0 + 30.0, score=1.0 - i) for i in range(6)]
    assert len(segments.select_clips(candidates, count=2)) == 2
    assert segments.select_clips(candidates, count=0) == []


# -- Re-timing for subtitles -------------------------------------------


def test_words_in_span_are_rebased_to_the_clip_timeline():
    parsed = segments.parse_words(
        [
            {"start": 10.0, "end": 10.5, "word": "inside"},
            {"start": 30.0, "end": 30.5, "word": "outside"},
        ]
    )
    picked = segments.words_in_span(parsed, 9.0, 20.0)
    assert [w.text for w in picked] == ["inside"]
    assert picked[0].start == pytest.approx(1.0)
    assert picked[0].end == pytest.approx(1.5)


def test_word_straddling_the_end_is_clipped_to_the_span():
    parsed = segments.parse_words([{"start": 19.0, "end": 21.0, "word": "straddle"}])
    picked = segments.words_in_span(parsed, 10.0, 20.0)
    assert picked[0].end == pytest.approx(10.0)


# -- Trend profile -----------------------------------------------------


def reference_pool(winner_trait, loser_trait, n=40):
    """A pool where winners carry one title trait and losers carry another."""
    rows = []
    for i in range(n):
        rows.append(
            {
                "origin": "reference",
                "channel": "c",
                "title": winner_trait,
                "duration_seconds": 40.0,
                "views": 1000.0 + i,
                "normalized_views": 5.0 + i,
            }
        )
        rows.append(
            {
                "origin": "reference",
                "channel": "c",
                "title": loser_trait,
                "duration_seconds": 120.0,
                "views": 10.0,
                "normalized_views": 0.1,
            }
        )
    return rows


def test_profile_abstains_when_the_pool_is_tiny():
    built = trends.build_profile(
        [{"title": "x", "normalized_views": 1.0, "duration_seconds": 30.0, "channel": "c"}]
    )
    assert built["ready"] is False
    assert "need about" in built["reason"]


def test_profile_finds_the_trait_that_separates_winners():
    built = trends.build_profile(reference_pool("Que pasa si?", "Un titulo plano"))
    assert built["ready"] is True
    assert built["title_traits"]["question"]["lift"] > 1.25
    assert built["title_traits"]["question"]["meaningful"] is True


def test_profile_marks_a_shared_trait_as_noise():
    """A trait every video carries explains nothing and must not be advice."""
    built = trends.build_profile(reference_pool("Titulo #tag", "Otro #tag"))
    assert built["title_traits"]["has_hashtag"]["lift"] == pytest.approx(1.0, abs=0.05)
    assert built["title_traits"]["has_hashtag"]["meaningful"] is False


def test_profile_reports_winning_durations():
    built = trends.build_profile(reference_pool("Que pasa si?", "Un titulo plano"))
    assert built["duration_seconds"]["median"] == pytest.approx(40.0)


def test_channel_with_too_few_shorts_is_skipped():
    account = {
        "nickname": "tiny",
        "videos": [
            {"is_short": True, "view_count": 100, "duration_seconds": 30, "title": "a"}
            for _ in range(5)
        ],
    }
    assert trends.normalized_shorts([account]) == []


def test_views_are_normalized_against_the_channels_own_median():
    account = {
        "nickname": "c",
        "videos": [
            {"is_short": True, "view_count": 100, "duration_seconds": 30, "title": "a"}
            for _ in range(20)
        ]
        + [{"is_short": True, "view_count": 1000, "duration_seconds": 30, "title": "hit"}],
    }
    rows = trends.normalized_shorts([account])
    hit = next(r for r in rows if r["title"] == "hit")
    assert hit["normalized_views"] == pytest.approx(10.0)


def test_duration_window_keeps_a_usable_width_against_the_ceiling():
    """A profile pointing above the ceiling must not collapse the window."""
    prof = {"ready": True, "duration_seconds": {"p25": 57.0, "p75": 141.0}}
    low, high = trends.duration_window(prof)
    assert high <= 60.0
    assert high - low >= trends.MIN_WINDOW_WIDTH - 0.01


def test_duration_window_falls_back_when_the_profile_is_not_ready():
    low, high = trends.duration_window({"ready": False})
    assert low < high


def test_prompt_brief_says_so_when_it_knows_nothing():
    assert "No trend profile yet" in trends.prompt_brief({"ready": False, "reason": "x"})


# -- Scoring -----------------------------------------------------------

from clips import render, scoring  # noqa: E402


def test_truncated_model_reply_still_yields_the_complete_objects():
    """Free models stop at their output limit without closing the array."""
    reply = (
        '[{"id":0,"hook":7,"payoff":5,"standalone":8,"title":"A"},'
        '{"id":1,"hook":4,"payoff":3,"standalone":3,"title":"B"},'
        '{"id":2,"hook":9,"pay'
    )
    parsed = scoring._parse_json_from_llm(reply)
    assert [item["id"] for item in parsed] == [0, 1]


def test_fenced_model_reply_is_parsed():
    reply = 'Sure:\n```json\n[{"id": 0, "hook": 5}]\n```'
    assert scoring._parse_json_from_llm(reply) == [{"id": 0, "hook": 5}]


def test_model_scores_ignore_bad_and_repeated_indices():
    candidates = [window(0.0, 30.0), window(40.0, 70.0)]
    applied = scoring.apply_llm_scores(
        candidates,
        [
            {"id": 0, "hook": 10, "payoff": 10, "standalone": 10, "title": "Win"},
            {"id": 0, "hook": 0, "payoff": 0, "standalone": 0, "title": "Dup"},
            {"id": 7, "hook": 10},
            {"id": "x"},
            "junk",
        ],
    )
    assert applied == 1
    assert candidates[0].title == "Win"
    assert candidates[0].scores["llm"] == pytest.approx(1.0)
    assert "llm" not in candidates[1].scores


def test_a_span_opening_mid_argument_scores_below_a_clean_one():
    clean = Window(0.0, 40.0, "Nadie esperaba lo que la sonda encontro.", 0, 0)
    dangling = Window(0.0, 40.0, "Pero entonces la sonda encontro algo.", 0, 0)
    assert (
        scoring.heuristic_score(clean, 30.0, 50.0)["total"]
        > scoring.heuristic_score(dangling, 30.0, 50.0)["total"]
    )


def test_length_fit_peaks_inside_the_band_and_fades_outside():
    assert scoring.length_fit(40.0, 30.0, 50.0) == pytest.approx(1.0)
    assert scoring.length_fit(40.0, 30.0, 50.0) > scoring.length_fit(55.0, 30.0, 50.0)
    assert scoring.length_fit(120.0, 30.0, 50.0) == 0.0


def test_heuristics_only_run_when_model_disabled():
    candidates = [Window(0.0, 40.0, "Hola mundo.", 0, 0)]
    report = scoring.score_windows(candidates, 30.0, 50.0, use_llm=False)
    assert report["llm"] is False
    assert candidates[0].score > 0


# -- Subtitle lines ----------------------------------------------------


def test_subtitle_lines_group_words_and_break_on_sentence_end():
    timed = [
        Word(0.0, 0.3, "Hola."),
        Word(0.4, 0.7, "uno"),
        Word(0.8, 1.1, "dos"),
        Word(1.2, 1.5, "tres"),
        Word(1.6, 1.9, "cuatro"),
    ]
    lines = render.group_lines(timed, per_line=3)
    assert [text for _, _, text in lines] == ["Hola.", "uno dos tres", "cuatro"]


def test_subtitle_lines_never_overlap():
    timed = [Word(i * 0.1, i * 0.1 + 0.05, f"w{i}") for i in range(9)]
    lines = render.group_lines(timed, per_line=3)
    for (_, end, _), (next_start, _, _) in zip(lines, lines[1:]):
        assert end <= next_start + 1e-9


def test_ass_timestamps_use_centiseconds():
    assert render._timestamp(3661.5) == "1:01:01.50"
    assert render._timestamp(-2) == "0:00:00.00"


# -- Freshness and ingest ----------------------------------------------

import os  # noqa: E402

from clips import ingest  # noqa: E402


def test_profile_is_stale_when_a_history_file_is_newer(tmp_path):
    built = tmp_path / "trend_profile.json"
    history = tmp_path / "reference_channels.json"
    built.write_text("{}")
    history.write_text("{}")
    os.utime(built, (1000, 1000))
    os.utime(history, (2000, 2000))
    assert trends.is_stale(str(built), [str(history)]) is True
    os.utime(built, (3000, 3000))
    assert trends.is_stale(str(built), [str(history)]) is False


def test_missing_profile_is_stale(tmp_path):
    assert trends.is_stale(str(tmp_path / "nope.json"), []) is True


def test_unmerged_download_fragments_are_never_picked(tmp_path):
    """A failed merge leaves a big video-only fragment; it must not be used."""
    (tmp_path / "source.f137.mp4").write_bytes(b"x" * 5000)
    (tmp_path / "source.f140.m4a").write_bytes(b"x" * 100)
    with pytest.raises(ingest.IngestError):
        ingest._find_video(str(tmp_path))


def test_merged_download_is_preferred(tmp_path):
    (tmp_path / "source.f137.mp4").write_bytes(b"x" * 5000)
    (tmp_path / "source.mp4").write_bytes(b"x" * 10)
    assert ingest._find_video(str(tmp_path)).endswith("source.mp4")


def test_one_video_keeps_one_id_however_its_link_was_copied():
    """A timestamp or a share tag must not buy a second download."""
    watch = ingest.source_id_for("https://www.youtube.com/watch?v=SrX1ZrJYFtA")
    for spelling in (
        "https://www.youtube.com/watch?v=SrX1ZrJYFtA&t=6s",
        "https://youtu.be/SrX1ZrJYFtA?si=aBcD1234EfGh&t=6",
        "https://m.youtube.com/watch?v=SrX1ZrJYFtA",
    ):
        assert ingest.source_id_for(spelling) == watch, spelling
    # Two different videos still get two directories, and a host this does
    # not know about keeps the parameters that identify its video.
    assert ingest.source_id_for("https://youtu.be/aaaaaaaaaaa") != watch
    # `start` is dropped on YouTube, where it only moves the playhead. On a
    # host whose grammar we do not know it may be what picks the resource, so
    # there it has to survive.
    assert ingest.source_id_for("https://vimeo.com/x?start=1") != ingest.source_id_for(
        "https://vimeo.com/x?start=2"
    )
    assert ingest.source_id_for("https://vimeo.com/x?clip=1") != ingest.source_id_for(
        "https://vimeo.com/x?clip=2"
    )


def test_url_ids_are_stable_and_local_ids_differ_per_file(tmp_path):
    assert ingest.source_id_for("https://a/b") == ingest.source_id_for(" https://a/b ")
    one, two = tmp_path / "a", tmp_path / "b"
    one.mkdir()
    two.mkdir()
    (one / "video.mp4").write_bytes(b"1")
    (two / "video.mp4").write_bytes(b"2")
    assert (
        ingest.from_local(str(one / "video.mp4")).id
        != ingest.from_local(str(two / "video.mp4")).id
    )
