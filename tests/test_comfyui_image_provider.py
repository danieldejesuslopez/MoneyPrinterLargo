import json
import sys
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import comfyui_image_provider as provider  # noqa: E402


def _png_bytes(width: int, height: int) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), (30, 90, 160)).save(buffer, "PNG")
    return buffer.getvalue()


# --- native render sizing ---------------------------------------------------

@pytest.mark.parametrize(
    "canvas,max_side",
    [((2160, 3840), 1216), ((1080, 1920), 1216), ((3840, 2160), 1216), ((1024, 1024), 1536)],
)
def test_native_size_is_vae_friendly_and_capped(canvas, max_side):
    width, height = provider._native_render_size(*canvas, max_side)
    assert width % 64 == 0 and height % 64 == 0
    assert max(width, height) <= max_side + 64
    # orientation must survive
    assert (width > height) == (canvas[0] > canvas[1])


def test_native_size_never_upscales_past_the_canvas():
    # A small canvas with a generous max_side should not be rendered larger
    # than the canvas it will be downscaled into.
    width, height = provider._native_render_size(512, 512, 2048)
    assert (width, height) == (512, 512)


def test_native_size_rejects_degenerate_canvas():
    with pytest.raises(ValueError):
        provider._native_render_size(0, 100, 1216)


# --- canvas fitting ---------------------------------------------------------

def test_fit_to_canvas_returns_exact_dimensions():
    result = provider._fit_to_canvas(_png_bytes(704, 1216), 2160, 3840)
    with Image.open(BytesIO(result)) as img:
        assert img.size == (2160, 3840)


def test_fit_to_canvas_rejects_empty_payload():
    with pytest.raises(provider.ComfyUIError):
        provider._fit_to_canvas(b"", 100, 100)


# --- workflow handling ------------------------------------------------------

def test_default_workflow_wires_a_complete_graph():
    graph = provider._default_workflow(
        prompt="a fox", negative="blurry", width=832, height=1216, seed=7,
        checkpoint="model.safetensors", steps=28, cfg=7.0,
        sampler="dpmpp_2m", scheduler="karras",
    )
    assert graph["5"]["inputs"]["seed"] == 7
    assert graph["7"]["class_type"] == "SaveImage"
    # every node reference must point at a node that exists
    for node in graph.values():
        for value in node["inputs"].values():
            if isinstance(value, list):
                assert value[0] in graph


def test_custom_workflow_substitution_preserves_types(tmp_path):
    workflow = tmp_path / "flux.json"
    workflow.write_text(json.dumps({
        "9": {"class_type": "CLIPTextEncode", "inputs": {"text": "cinematic, %PROMPT%, grain"}},
        "10": {"class_type": "EmptySD3LatentImage",
               "inputs": {"width": "%WIDTH%", "height": "%HEIGHT%"}},
        "11": {"class_type": "KSampler", "inputs": {"seed": "%SEED%"}},
    }), encoding="utf-8")

    graph = provider._load_custom_workflow(
        str(workflow), prompt="a fox", negative="", width=832, height=1216, seed=7
    )

    # ComfyUI validates input types, so a bare token must yield an int
    assert graph["10"]["inputs"]["width"] == 832
    assert isinstance(graph["10"]["inputs"]["width"], int)
    assert graph["11"]["inputs"]["seed"] == 7
    # an embedded token is interpolated as text
    assert graph["9"]["inputs"]["text"] == "cinematic, a fox, grain"


def test_ui_format_export_is_rejected_with_guidance(tmp_path):
    workflow = tmp_path / "ui.json"
    workflow.write_text(json.dumps({"nodes": [{"id": 1}], "links": []}), encoding="utf-8")

    with pytest.raises(provider.ComfyUIError, match="Export \\(API\\)"):
        provider._load_custom_workflow(
            str(workflow), prompt="x", negative="", width=64, height=64, seed=1
        )


def test_missing_workflow_file_is_reported(tmp_path):
    with pytest.raises(provider.ComfyUIError, match="not found"):
        provider._load_custom_workflow(
            str(tmp_path / "nope.json"), prompt="x", negative="", width=64, height=64, seed=1
        )


# --- end to end over a stubbed HTTP layer -----------------------------------

class _Response:
    def __init__(self, payload=None, content=b"", status_code=200):
        self._payload = payload
        self.content = content
        self.status_code = status_code
        self.text = ""

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"HTTP {self.status_code}")


def test_generate_queues_polls_and_returns_canvas_sized_png(monkeypatch):
    queued = {}

    def fake_post(url, json=None, timeout=None):
        assert url.endswith("/prompt")
        assert json["client_id"]
        queued.update(json["prompt"])
        return _Response({"prompt_id": "abc"})

    calls = {"history": 0}

    def fake_get(url, timeout=None):
        if "/history/" in url:
            calls["history"] += 1
            if calls["history"] < 2:
                return _Response({})  # still rendering
            return _Response({"abc": {
                "status": {"status_str": "success", "completed": True},
                "outputs": {"7": {"images": [
                    {"filename": "MoneyPrinter_00001_.png", "subfolder": "", "type": "output"}
                ]}},
            }})
        if "/view" in url:
            return _Response(content=_png_bytes(704, 1216))
        raise AssertionError(f"unexpected GET {url}")

    monkeypatch.setattr(provider.requests, "post", fake_post)
    monkeypatch.setattr(provider.requests, "get", fake_get)
    monkeypatch.setattr(provider, "get_comfyui_checkpoint", lambda: "model.safetensors")
    monkeypatch.setattr(provider, "get_comfyui_workflow", lambda: "")
    monkeypatch.setattr(provider, "_POLL_INTERVAL_SECONDS", 0)

    result = provider.generate_image_bytes_with_comfyui(
        "a lighthouse", width=1080, height=1920, seed=42
    )

    with Image.open(BytesIO(result)) as img:
        assert img.size == (1080, 1920)
    assert calls["history"] >= 2, "should have polled until outputs appeared"
    assert queued["5"]["inputs"]["seed"] == 42


def test_generate_without_checkpoint_or_workflow_raises(monkeypatch):
    monkeypatch.setattr(provider, "get_comfyui_checkpoint", lambda: "")
    monkeypatch.setattr(provider, "get_comfyui_workflow", lambda: "")
    monkeypatch.setattr(provider, "list_checkpoints", lambda: ["a.safetensors"])

    with pytest.raises(provider.ComfyUIError, match="comfyui_checkpoint"):
        provider.generate_image_bytes_with_comfyui("x", width=64, height=64)


def test_workflow_without_saveimage_is_reported():
    entry = {"outputs": {"9": {"images": [{"filename": "p.png", "type": "temp"}]}}}
    with pytest.raises(provider.ComfyUIError, match="SaveImage"):
        provider._download_first_image(entry)
