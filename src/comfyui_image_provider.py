"""
Local GPU image generation through a running ComfyUI instance.

Unlike the Gemini/Codex/Leonardo providers, this one never leaves the machine:
it talks to the ComfyUI HTTP API (default http://127.0.0.1:8188) and renders on
the local graphics card. Designed for AMD RDNA4 (RX 9070 / 9070 XT, gfx1201)
running ComfyUI on native Windows ROCm, but it is backend-agnostic -- anything
ComfyUI can drive will work.

Two modes:

1. Built-in workflow (default). A plain checkpoint text2img graph:
   CheckpointLoaderSimple -> CLIPTextEncode x2 -> EmptyLatentImage -> KSampler
   -> VAEDecode -> SaveImage. Works with any SDXL / SD1.5 / Illustrious /
   Pony checkpoint sitting in ComfyUI/models/checkpoints.

2. Custom workflow. Point `comfyui_workflow` at a workflow exported from
   ComfyUI in *API format* (Workflow -> Export (API)). Placeholder tokens
   %PROMPT%, %NEGATIVE%, %WIDTH%, %HEIGHT% and %SEED% are substituted before
   the graph is queued. Use this for Flux, Qwen-Image, SD3.5 or anything with
   a non-standard loader chain.

Resolution strategy: diffusion models fall apart when asked for their target
canvas directly (a 2160x3840 SDXL render is a smeared mess and will OOM on
16GB). We render at the model's native sweet spot, preserving aspect ratio,
then upscale with LANCZOS to the requested canvas.
"""

from __future__ import annotations

import json
import random
import time
import urllib.parse
import uuid
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image

from config import (
    get_comfyui_base_url,
    get_comfyui_cfg,
    get_comfyui_checkpoint,
    get_comfyui_negative_prompt,
    get_comfyui_native_max_side,
    get_comfyui_sampler,
    get_comfyui_scheduler,
    get_comfyui_steps,
    get_comfyui_timeout_seconds,
    get_comfyui_workflow,
)

# ComfyUI reports progress over a websocket, but polling /history is dependency
# free and plenty responsive for 20-40 step renders.
_POLL_INTERVAL_SECONDS = 1.0


class ComfyUIError(RuntimeError):
    """Raised when ComfyUI is unreachable or returns something unusable."""


# ---------------------------------------------------------------------------
# Sizing
# ---------------------------------------------------------------------------

def _native_render_size(width: int, height: int, max_side: int) -> tuple[int, int]:
    """Pick a diffusion-friendly render size with the same aspect as the canvas.

    Both dimensions are snapped to a multiple of 64, which every SD-family VAE
    expects, and the long edge is capped at `max_side` so we stay inside VRAM.
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"Invalid canvas: {width}x{height}")

    # Never render larger than the canvas we are about to downscale into --
    # that is pure VRAM and wall-clock cost for detail we throw away.
    scale = min(max_side / float(max(width, height)), 1.0)
    render_w = max(64, int(round(width * scale / 64.0)) * 64)
    render_h = max(64, int(round(height * scale / 64.0)) * 64)
    return render_w, render_h


def _fit_to_canvas(image_bytes: bytes, width: int, height: int) -> bytes:
    """Centre-crop to the canvas aspect, then resize to the exact canvas."""
    if not image_bytes or len(image_bytes) < 1000:
        raise ComfyUIError("ComfyUI returned an empty image")

    with Image.open(BytesIO(image_bytes)) as img:
        img.load()
        img = img.convert("RGB")

        target_aspect = width / height
        source_aspect = img.width / img.height
        if source_aspect > target_aspect:
            crop_w = max(1, int(round(img.height * target_aspect)))
            left = max(0, (img.width - crop_w) // 2)
            img = img.crop((left, 0, left + crop_w, img.height))
        elif source_aspect < target_aspect:
            crop_h = max(1, int(round(img.width / target_aspect)))
            top = max(0, (img.height - crop_h) // 2)
            img = img.crop((0, top, img.width, top + crop_h))

        if img.size != (width, height):
            img = img.resize((width, height), Image.LANCZOS)

        out = BytesIO()
        img.save(out, format="PNG")
        return out.getvalue()


# ---------------------------------------------------------------------------
# Workflow construction
# ---------------------------------------------------------------------------

def _default_workflow(
    *,
    prompt: str,
    negative: str,
    width: int,
    height: int,
    seed: int,
    checkpoint: str,
    steps: int,
    cfg: float,
    sampler: str,
    scheduler: str,
) -> dict:
    """A minimal checkpoint text2img graph in ComfyUI API format."""
    return {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": checkpoint},
        },
        "2": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt, "clip": ["1", 1]},
        },
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": negative, "clip": ["1", 1]},
        },
        "4": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        },
        "5": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": sampler,
                "scheduler": scheduler,
                "denoise": 1.0,
                "model": ["1", 0],
                "positive": ["2", 0],
                "negative": ["3", 0],
                "latent_image": ["4", 0],
            },
        },
        "6": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["5", 0], "vae": ["1", 2]},
        },
        "7": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": "MoneyPrinter", "images": ["6", 0]},
        },
    }


def _substitute(node: object, replacements: dict[str, object]) -> object:
    """Recursively swap %TOKEN% placeholders inside a workflow graph.

    A string that is *exactly* a token becomes the raw typed value (so %WIDTH%
    yields the int 1024, not the string "1024" -- ComfyUI validates types).
    Tokens embedded in a longer string are interpolated as text, which lets you
    write things like "cinematic, %PROMPT%, film grain" in your own workflow.
    """
    if isinstance(node, dict):
        return {k: _substitute(v, replacements) for k, v in node.items()}
    if isinstance(node, list):
        return [_substitute(v, replacements) for v in node]
    if isinstance(node, str):
        stripped = node.strip()
        if stripped in replacements:
            return replacements[stripped]
        for token, value in replacements.items():
            if token in node:
                node = node.replace(token, str(value))
        return node
    return node


def _load_custom_workflow(
    path: str,
    *,
    prompt: str,
    negative: str,
    width: int,
    height: int,
    seed: int,
) -> dict:
    workflow_path = Path(path)
    if not workflow_path.is_file():
        raise ComfyUIError(f"comfyui_workflow not found: {workflow_path}")

    try:
        graph = json.loads(workflow_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ComfyUIError(f"comfyui_workflow is not valid JSON: {exc}") from exc

    # A UI-format export has a top-level "nodes" list; the API format is a flat
    # dict keyed by node id. Only the latter can be queued.
    if "nodes" in graph and isinstance(graph.get("nodes"), list):
        raise ComfyUIError(
            f"{workflow_path.name} looks like a UI export. In ComfyUI use "
            "Workflow -> Export (API) instead of Export/Save."
        )

    return _substitute(
        graph,
        {
            "%PROMPT%": prompt,
            "%NEGATIVE%": negative,
            "%WIDTH%": width,
            "%HEIGHT%": height,
            "%SEED%": seed,
        },
    )


# ---------------------------------------------------------------------------
# ComfyUI HTTP API
# ---------------------------------------------------------------------------

def _api_root() -> str:
    return get_comfyui_base_url().rstrip("/")


def ping() -> dict:
    """Return ComfyUI's system stats, or raise if it is not running."""
    try:
        response = requests.get(f"{_api_root()}/system_stats", timeout=10)
    except requests.RequestException as exc:
        raise ComfyUIError(
            f"ComfyUI is not reachable at {_api_root()}. Start it first "
            f"(python main.py --listen 127.0.0.1 --port 8188). Detail: {exc}"
        ) from exc
    if response.status_code >= 400:
        raise ComfyUIError(f"ComfyUI HTTP {response.status_code}: {response.text[:300]}")
    return response.json()


def list_checkpoints() -> list[str]:
    """Names of the checkpoints ComfyUI can currently load."""
    try:
        response = requests.get(f"{_api_root()}/object_info/CheckpointLoaderSimple", timeout=15)
        response.raise_for_status()
        info = response.json()
        options = info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0]
        return [str(name) for name in options]
    except Exception:
        return []


def _queue(workflow: dict, client_id: str) -> str:
    try:
        response = requests.post(
            f"{_api_root()}/prompt",
            json={"prompt": workflow, "client_id": client_id},
            timeout=60,
        )
    except requests.RequestException as exc:
        raise ComfyUIError(f"Could not queue the workflow: {exc}") from exc

    if response.status_code >= 400:
        # ComfyUI puts the useful validation message in the body, not the status.
        raise ComfyUIError(f"ComfyUI rejected the workflow (HTTP {response.status_code}): {response.text[:800]}")

    prompt_id = response.json().get("prompt_id")
    if not prompt_id:
        raise ComfyUIError(f"ComfyUI returned no prompt_id: {response.text[:300]}")
    return str(prompt_id)


def _await_history(prompt_id: str, timeout_seconds: int) -> dict:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            response = requests.get(f"{_api_root()}/history/{prompt_id}", timeout=15)
            response.raise_for_status()
            history = response.json()
        except requests.RequestException:
            time.sleep(_POLL_INTERVAL_SECONDS)
            continue

        entry = history.get(prompt_id)
        if entry:
            status = entry.get("status") or {}
            if status.get("status_str") == "error" or status.get("completed") is False:
                messages = status.get("messages") or []
                raise ComfyUIError(f"ComfyUI execution failed: {str(messages)[:800]}")
            if entry.get("outputs"):
                return entry

        time.sleep(_POLL_INTERVAL_SECONDS)

    raise ComfyUIError(
        f"ComfyUI did not finish within {timeout_seconds}s. Raise "
        "comfyui_timeout_seconds, lower comfyui_steps, or reduce "
        "comfyui_native_max_side."
    )


def _download_first_image(entry: dict) -> bytes:
    for output in (entry.get("outputs") or {}).values():
        for image in output.get("images") or []:
            if image.get("type") == "temp":
                continue
            query = urllib.parse.urlencode({
                "filename": image.get("filename", ""),
                "subfolder": image.get("subfolder", ""),
                "type": image.get("type", "output"),
            })
            response = requests.get(f"{_api_root()}/view?{query}", timeout=120)
            response.raise_for_status()
            return response.content
    raise ComfyUIError(
        "The workflow completed but produced no saved image. Make sure the "
        "graph ends in a SaveImage node (PreviewImage output is not retrievable)."
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate_image_bytes_with_comfyui(
    prompt: str,
    *,
    width: int,
    height: int,
    seed: int | None = None,
) -> bytes:
    """Render one PNG on the local GPU and return it at exactly width x height."""
    checkpoint = get_comfyui_checkpoint()
    custom_workflow = get_comfyui_workflow()

    if not checkpoint and not custom_workflow:
        available = list_checkpoints()
        hint = f" Available: {', '.join(available[:8])}" if available else ""
        raise ComfyUIError(
            "No checkpoint configured. Set comfyui_checkpoint in config.json "
            f"(or point comfyui_workflow at an API-format workflow).{hint}"
        )

    render_w, render_h = _native_render_size(width, height, get_comfyui_native_max_side())
    resolved_seed = random.randint(0, 2**31 - 1) if seed is None else int(seed)
    negative = get_comfyui_negative_prompt()
    # ComfyUI's CLIP encoder silently truncates past 77 tokens; trimming here
    # keeps the log honest about what was actually rendered.
    trimmed_prompt = prompt[:2000]

    if custom_workflow:
        workflow = _load_custom_workflow(
            custom_workflow,
            prompt=trimmed_prompt,
            negative=negative,
            width=render_w,
            height=render_h,
            seed=resolved_seed,
        )
    else:
        workflow = _default_workflow(
            prompt=trimmed_prompt,
            negative=negative,
            width=render_w,
            height=render_h,
            seed=resolved_seed,
            checkpoint=checkpoint,
            steps=get_comfyui_steps(),
            cfg=get_comfyui_cfg(),
            sampler=get_comfyui_sampler(),
            scheduler=get_comfyui_scheduler(),
        )

    client_id = str(uuid.uuid4())
    prompt_id = _queue(workflow, client_id)
    entry = _await_history(prompt_id, get_comfyui_timeout_seconds())
    raw = _download_first_image(entry)
    return _fit_to_canvas(raw, width, height)
