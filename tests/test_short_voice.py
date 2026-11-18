import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from classes.Tts import (
    SHORT_VIDEO_NARRATOR,
    is_spanish_language,
    resolve_voice_id,
    short_voice_for_language,
)


@pytest.mark.parametrize(
    "tts_attributes, voice_id, expected",
    [
        pytest.param(
            {"_provider": "edge_tts", "_voice": "Jasper"},
            "",
            SHORT_VIDEO_NARRATOR,
            id="empty-override-english-global-alias",
        ),
        pytest.param(
            {"_provider": "edge_tts", "_voice": "en-US-GuyNeural"},
            "",
            SHORT_VIDEO_NARRATOR,
            id="empty-override-english-global-id",
        ),
        pytest.param({}, "", SHORT_VIDEO_NARRATOR, id="fake-without-provider-or-voice"),
        pytest.param(
            {"_voice": "Jasper"}, "", SHORT_VIDEO_NARRATOR, id="implicit-edge-provider"
        ),
        pytest.param(
            {"_provider": "edge_tts", "_voice": ""},
            "",
            SHORT_VIDEO_NARRATOR,
            id="empty-global-and-override",
        ),
        pytest.param(
            {"_provider": "edge_tts", "_voice": "Jasper"},
            "es-MX-DaliaNeural",
            "es-MX-DaliaNeural",
            id="spanish-override-preserved",
        ),
        pytest.param(
            {"_provider": "edge_tts", "_voice": "Pablo"},
            "",
            "",
            id="spanish-global-alias-preserves-default",
        ),
        pytest.param(
            {"_provider": "edge_tts", "_voice": "es-BO-SofiaNeural"},
            "",
            "",
            id="spanish-global-id-preserves-default",
        ),
        pytest.param(
            {"_provider": "edge_tts", "_voice": "Pablo"},
            "en-US-GuyNeural",
            SHORT_VIDEO_NARRATOR,
            id="english-override-takes-precedence-over-spanish-global",
        ),
        pytest.param(
            {"_provider": "kittentts", "_voice": "Jasper"},
            "",
            "",
            id="kitten-empty-override-preserved",
        ),
        pytest.param(
            {"_provider": "kittentts", "_voice": "Bella"},
            "Jasper",
            "Jasper",
            id="kitten-explicit-voice-preserved",
        ),
    ],
)
def test_short_voice_for_spanish_channel(tts_attributes, voice_id, expected):
    fake_tts = SimpleNamespace(**tts_attributes)

    assert short_voice_for_language(fake_tts, voice_id, "espanol") == expected


@pytest.mark.parametrize("voice_id", ["", "en-US-GuyNeural", "es-ES-AlvaroNeural"])
def test_short_voice_for_english_channel_preserves_override(voice_id):
    fake_tts = SimpleNamespace(_provider="edge_tts", _voice="Jasper")

    assert short_voice_for_language(fake_tts, voice_id, "english") == voice_id


def test_short_voice_for_esperanto_preserves_empty_override():
    fake_tts = SimpleNamespace(_provider="edge_tts", _voice="Jasper")

    assert short_voice_for_language(fake_tts, "", "esperanto") == ""


@pytest.mark.parametrize(
    "voice, expected",
    [
        (" Jasper ", "en-US-GuyNeural"),
        ("Pablo", "es-ES-AlvaroNeural"),
        ("es-MX-DaliaNeural", "es-MX-DaliaNeural"),
        ("", ""),
        (None, ""),
    ],
)
def test_resolve_voice_id(voice, expected):
    assert resolve_voice_id(voice) == expected


@pytest.mark.parametrize(
    "language",
    ["espanol", "espa\u00f1ol", "Espa\u00f1ol (MX)", "castellano", "Spanish", " es ", "es-BO", "es_MX"],
)
def test_is_spanish_language_accepts_channel_names_and_locale_ids(language):
    assert is_spanish_language(language)


@pytest.mark.parametrize("language", ["english", "en-US", "esperanto", "", None])
def test_is_spanish_language_rejects_non_spanish_and_empty_language(language):
    assert not is_spanish_language(language)
