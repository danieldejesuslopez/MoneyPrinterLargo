"""
Stability AI (Stable Diffusion) image generation via the hosted REST API.

Uses the Stable Image v2beta endpoints. The key is read from
`stability_api_key` in config.json or the STABILITY_API_KEY environment
variable - never hardcode it. The model renders at its native 9:16 size and
the caller's canvas is reached with a LANCZOS resize.
"""

from __future__ import annotations

from io import BytesIO

import requests
from PIL import Image

from config import get_stability_api_key, get_stability_model

_ENDPOINTS = {
    "core": "https://api.stability.ai/v2beta/stable-image/generate/core",
    "ultra": "https://api.stability.ai/v2beta/stable-image/generate/ultra",
    "sd3": "https://api.stability.ai/v2beta/stable-image/generate/sd3",
}


class StabilityError(RuntimeError):
    pass


def _aspect_ratio(width: int, height: int) -> str:
    options = {"9:16": 9 / 16, "16:9": 16 / 9, "1:1": 1.0, "2:3": 2 / 3,
               "3:2": 1.5, "4:5": 0.8, "5:4": 1.25, "9:21": 9 / 21, "21:9": 21 / 9}
    ratio = width / max(height, 1)
    return min(options, key=lambda name: abs(options[name] - ratio))


def generate_image_bytes_with_stability(prompt: str, width: int = 1080, height: int = 1920) -> bytes:
    api_key = get_stability_api_key()
    if not api_key:
        raise StabilityError("stability_api_key is empty (config.json or STABILITY_API_KEY)")
    model = get_stability_model()
    url = _ENDPOINTS.get(model, _ENDPOINTS["core"])

    response = requests.post(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Accept": "image/*"},
        files={"none": ""},
        data={
            "prompt": prompt[:10000],
            "aspect_ratio": _aspect_ratio(width, height),
            "output_format": "png",
            "negative_prompt": "text, watermark, logo, blurry, deformed, low quality",
        },
        timeout=180,
    )
    if response.status_code != 200:
        raise StabilityError(f"Stability API {response.status_code}: {response.text[:300]}")

    image = Image.open(BytesIO(response.content)).convert("RGB")
    if image.size != (width, height):
        image = image.resize((width, height), Image.LANCZOS)
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()
