"""Captions fetched with a download: which track, how it is read, when used.

The track policy is the part with consequences: a machine-translated
caption burned onto a clip would put words in someone's mouth, so the
rule that generated captions are only taken in the original language is
tested here, not left to the platform's defaults.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clips import ingest, pipeline, transcribe  # noqa: E402


# -- Track policy ------------------------------------------------------


def info(language="es", manual=(), auto=()):
    return {
        "language": language,
        "subtitles": {key: [{}] for key in manual},
        "automatic_captions": {key: [{}] for key in auto},
    }


def test_person_written_captions_win_over_generated():
    track = ingest.choose_caption_track(info(manual=["es"], auto=["es-orig", "es"]))
    assert track == {"lang": "es", "kind": "manual"}


def test_generated_captions_prefer_the_original_recognition_track():
    track = ingest.choose_caption_track(info(auto=["en", "es", "es-orig", "fr"]))
    assert track == {"lang": "es-orig", "kind": "auto"}


def test_generated_captions_in_another_language_are_translations_and_refused():
    assert ingest.choose_caption_track(info(auto=["en", "es-orig"]), "en") is None


def test_a_person_written_translation_is_allowed():
    track = ingest.choose_caption_track(info(manual=["en"], auto=["es-orig"]), "en")
    assert track == {"lang": "en", "kind": "manual"}


def test_regional_variants_count_as_the_language():
    track = ingest.choose_caption_track(info(manual=["es-419"]), "es")
    assert track == {"lang": "es-419", "kind": "manual"}


def test_unknown_original_language_refuses_generated_tracks():
    assert ingest.choose_caption_track(info(language=None, auto=["es"]), "es") is None


def test_live_chat_is_not_a_caption_track():
    assert ingest.choose_caption_track(info(manual=["live_chat"])) is None


def test_no_captions_at_all():
    assert ingest.choose_caption_track(info()) is None


# -- Reading generated (rolling) captions ------------------------------


GENERATED = "\n".join(
    [
        "WEBVTT",
        "Kind: captions",
        "Language: es",
        "",
        "00:00:00.000 --> 00:00:02.230 align:start position:0%",
        " ",
        "¿Y<00:00:00.320><c> si</c><00:00:00.440><c> el</c><00:00:00.520><c> vecino</c>",
        "",
        "00:00:02.230 --> 00:00:02.240 align:start position:0%",
        "¿Y si el vecino",
        " ",
        "",
        "00:00:02.240 --> 00:00:03.830 align:start position:0%",
        "¿Y si el vecino",
        "fuera<00:00:02.520><c> en</c><00:00:02.600><c> realidad</c>",
        "",
        "00:00:03.830 --> 00:00:03.840 align:start position:0%",
        "fuera en realidad",
        " ",
        "",
        "00:00:03.840 --> 00:00:05.270 align:start position:0%",
        "fuera en realidad",
        "eléctrico?<00:00:04.584><c> [música]</c>",
        "",
    ]
)


@pytest.fixture
def generated(tmp_path):
    path = tmp_path / "captions.es-orig.vtt"
    path.write_text(GENERATED, encoding="utf-8")
    return transcribe.words_from_captions(str(path))


def test_rolling_lines_are_read_once(generated):
    assert [w["word"] for w in generated] == [
        "¿Y", "si", "el", "vecino", "fuera", "en", "realidad", "eléctrico?",
    ]


def test_generated_words_keep_their_real_timing(generated):
    by_word = {w["word"]: w for w in generated}
    assert by_word["¿Y"]["start"] == pytest.approx(0.0)
    assert by_word["¿Y"]["end"] == pytest.approx(0.32)
    assert by_word["si"]["start"] == pytest.approx(0.32)
    # The last word of a cue runs to the cue's end.
    assert by_word["vecino"]["end"] == pytest.approx(2.23)
    assert by_word["fuera"]["start"] == pytest.approx(2.24)


def test_sound_tags_in_generated_captions_are_dropped(generated):
    assert all("música" not in w["word"] for w in generated)


def test_trimmed_leading_space_lines_do_not_lose_cues(tmp_path):
    """Editors that strip trailing spaces turn the " " line into an empty one."""
    trimmed = "\n".join(line.rstrip() for line in GENERATED.split("\n"))
    path = tmp_path / "trimmed.vtt"
    path.write_text(trimmed, encoding="utf-8")
    words = transcribe.words_from_captions(str(path))
    assert [w["word"] for w in words][:4] == ["¿Y", "si", "el", "vecino"]


def test_generated_words_are_in_order(generated):
    starts = [w["start"] for w in generated]
    assert starts == sorted(starts)


# -- Manifests and precedence ------------------------------------------


def test_a_manifest_from_before_captions_still_loads(tmp_path):
    video = tmp_path / "source.mp4"
    video.write_bytes(b"x")
    (tmp_path / "source.json").write_text(
        json.dumps({"id": "url-1", "path": str(video), "title": "t", "url": "u"}),
        encoding="utf-8",
    )
    source = ingest.load_manifest(str(tmp_path))
    assert source is not None
    assert source.captions_checked is False
    assert source.captions_path == ""


def test_fetched_captions_are_used_before_whisper(tmp_path, monkeypatch):
    captions = tmp_path / "captions.es-orig.vtt"
    captions.write_text(GENERATED, encoding="utf-8")
    source = ingest.Source(
        id="url-1",
        path=str(tmp_path / "source.mp4"),
        captions_path=str(captions),
        captions_kind="auto",
        captions_language="es-orig",
    )
    monkeypatch.setattr(
        transcribe, "transcribe", lambda *a, **k: pytest.fail("Whisper should not run")
    )
    result = pipeline._words_for(source, str(tmp_path), "es", "", False, lambda m: None)
    assert result["source"] == "captions:auto"
    assert result["word_count"] == 8


def test_whisper_flag_ignores_fetched_captions(tmp_path, monkeypatch):
    captions = tmp_path / "captions.es.vtt"
    captions.write_text(GENERATED, encoding="utf-8")
    source = ingest.Source(id="url-1", path="v.mp4", captions_path=str(captions))
    monkeypatch.setattr(
        transcribe,
        "transcribe",
        lambda *a, **k: {"words": [{"start": 0, "end": 1, "word": "w"}],
                         "word_count": 1, "language": "es"},
    )
    result = pipeline._words_for(source, str(tmp_path), "es", "whisper", False, lambda m: None)
    assert result["source"] == "whisper"


def test_an_explicit_caption_file_beats_the_fetched_one(tmp_path, monkeypatch):
    fetched = tmp_path / "captions.es.vtt"
    fetched.write_text(GENERATED, encoding="utf-8")
    mine = tmp_path / "mine.srt"
    mine.write_text("1\n00:00:01,000 --> 00:00:02,000\nHola mundo\n\n", encoding="utf-8")
    source = ingest.Source(id="url-1", path="v.mp4", captions_path=str(fetched),
                           captions_kind="manual")
    result = pipeline._words_for(source, str(tmp_path), "es", str(mine), False, lambda m: None)
    assert result["source"] == "captions:file"
    assert [w["word"] for w in result["words"]] == ["Hola", "mundo"]
