import importlib
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import codex_image_provider
import llm_provider
from classes.YouTube import YouTube


def test_codex_image_provider_does_not_inherit_claude_text_model(monkeypatch):
    captured = {}

    def fake_generate(prompt: str, *, width: int, height: int, model: str = "") -> bytes:
        captured["prompt"] = prompt
        captured["width"] = width
        captured["height"] = height
        captured["model"] = model
        return b"fake-png-bytes"

    monkeypatch.setattr(llm_provider, "get_active_provider", lambda: "claude")
    monkeypatch.setattr(llm_provider, "get_active_model", lambda: "opus")
    monkeypatch.setattr(codex_image_provider, "generate_image_bytes_with_codex", fake_generate)

    youtube = YouTube.__new__(YouTube)
    data = youtube._try_codex_cli_image("cinematic star field", 1080, 1920)

    assert data == b"fake-png-bytes"
    assert captured["model"] == ""
    assert captured["prompt"].startswith("Target canvas: 1080x1920px, HD, vertical 9:16.")
    assert captured["prompt"].endswith("cinematic star field")
    assert captured["width"] == 1080
    assert captured["height"] == 1920


def test_codex_image_provider_can_inherit_openai_text_model(monkeypatch):
    captured = {}

    def fake_generate(prompt: str, *, width: int, height: int, model: str = "") -> bytes:
        captured["model"] = model
        return b"fake-png-bytes"

    monkeypatch.setattr(llm_provider, "get_active_provider", lambda: "openai")
    monkeypatch.setattr(llm_provider, "get_active_model", lambda: "gpt-5.5")
    monkeypatch.setattr(codex_image_provider, "generate_image_bytes_with_codex", fake_generate)

    youtube = YouTube.__new__(YouTube)
    youtube._try_codex_cli_image("cinematic star field", 1080, 1920)

    assert captured["model"] == "gpt-5.5"


@pytest.mark.parametrize(
    ("last_result", "last_error"),
    [
        (RuntimeError("checkpoint missing"), "second provider: checkpoint missing"),
        (None, "second provider: returned no usable image"),
        (b"invalid image", "second provider: returned no usable image"),
    ],
    ids=["provider-exception", "empty-response", "undersized-response"],
)
def test_generate_images_batch_aborts_without_placeholder_when_all_providers_fail(
    monkeypatch, last_result, last_error
):
    youtube_module = importlib.import_module("classes.YouTube")
    youtube = YouTube.__new__(YouTube)
    youtube.images = []
    first_provider = Mock(side_effect=RuntimeError("first provider unavailable"))
    if isinstance(last_result, Exception):
        second_provider = Mock(side_effect=last_result)
    else:
        second_provider = Mock(return_value=last_result)
    ai_providers = Mock(return_value=[
        ("first provider", first_provider, "ai"),
        ("second provider", second_provider, "ai"),
    ])
    style_prompt = Mock(return_value="styled prompt")
    fallback_image = Mock()
    persist_image = Mock()
    monkeypatch.setattr(youtube, "_ai_image_providers", ai_providers)
    monkeypatch.setattr(youtube, "_ai_image_provider_choice", lambda: "comfyui")
    monkeypatch.setattr(youtube, "_apply_channel_style", style_prompt)
    monkeypatch.setattr(youtube, "_generate_fallback_image", fallback_image)
    monkeypatch.setattr(youtube, "_persist_image", persist_image)
    monkeypatch.setattr(youtube_module, "get_short_render_size", lambda: (1080, 1920))
    monkeypatch.setattr(youtube_module, "get_verbose", lambda: False)
    monkeypatch.setattr(youtube_module.time, "sleep", Mock())

    with pytest.raises(RuntimeError) as exc_info:
        youtube.generate_images_batch(["first scene", "second scene"])

    message = str(exc_info.value)
    assert "Image 1/2" in message
    assert "tried: first provider, second provider" in message
    assert f"last error: {last_error}" in message
    ai_providers.assert_called_once_with(1080, 1920)
    first_provider.assert_called_once_with("styled prompt")
    second_provider.assert_called_once_with("styled prompt")
    assert all(call.args == ("first scene",) for call in style_prompt.call_args_list)
    fallback_image.assert_not_called()
    persist_image.assert_not_called()
    assert youtube.images == []
