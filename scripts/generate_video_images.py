#!/usr/bin/env python3
"""
Generate the image set for one video on the local GPU. No TTS, no render.

Writes numbered PNGs to an output folder and drops a prompts.json alongside
them so a run can be reproduced or re-rolled image by image.

    # Let the LLM write the prompts from a topic
    python scripts/generate_video_images.py --topic "how black holes bend time" -n 8

    # Bring your own prompts, one per line
    python scripts/generate_video_images.py --prompts-file my_prompts.txt

    # Re-roll a single image from a previous run
    python scripts/generate_video_images.py --prompts-file out/prompts.json --only 3

Requires a running ComfyUI (see scripts/comfy_doctor.py).
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# main.py puts src/ on the path by running from the root; scripts must do it
# themselves so `from config import ...` resolves the same way.
sys.path.insert(0, os.path.join(ROOT_DIR, "src"))
os.chdir(ROOT_DIR)

from comfyui_image_provider import (  # noqa: E402
    ComfyUIError,
    generate_image_bytes_with_comfyui,
    list_checkpoints,
    ping,
)
from config import get_short_render_size  # noqa: E402


DEFAULT_STYLE = (
    "cinematic photography, dramatic volumetric lighting, shallow depth of field, "
    "rich colour grading, ultra detailed, sharp focus"
)


# ---------------------------------------------------------------------------
# Prompt sourcing
# ---------------------------------------------------------------------------

def _extract_json_array(raw: str) -> list[str]:
    """Pull a JSON string array out of an LLM response.

    Local models love to wrap answers in prose or ```json fences, so we look
    for the first bracketed array rather than trusting the whole payload.
    """
    fenced = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", raw, re.DOTALL)
    candidate = fenced.group(1) if fenced else None

    if candidate is None:
        start, end = raw.find("["), raw.rfind("]")
        if start == -1 or end <= start:
            raise ValueError("no JSON array found in the model output")
        candidate = raw[start:end + 1]

    parsed = json.loads(candidate)
    if not isinstance(parsed, list):
        raise ValueError("model returned JSON that is not an array")
    return [str(item).strip() for item in parsed if str(item).strip()]


def prompts_from_topic(topic: str, count: int, style: str) -> list[str]:
    from llm_provider import generate_text

    instruction = (
        f"You are writing image prompts for a {count}-shot vertical short video "
        f'about: "{topic}".\n\n'
        f"Return ONLY a JSON array of exactly {count} strings, nothing else.\n"
        "Each string is a self-contained visual description of one shot:\n"
        "- Describe only what is visible: subject, setting, camera angle, lighting, mood.\n"
        "- Never mention text, captions, letters, numbers, logos or watermarks.\n"
        "- Do not reference other shots ('the same person', 'as before'); each stands alone.\n"
        "- Keep each under 40 words.\n"
        "- Move the visual story forward from shot 1 to the last shot.\n"
    )

    print(f"Asking the LLM for {count} image prompts...")
    raw = generate_text(instruction)
    prompts = _extract_json_array(raw)

    if len(prompts) < count:
        print(f"  ! model returned {len(prompts)}, expected {count} -- using what it gave.")
    prompts = prompts[:count]

    return [f"{p.rstrip('. ')}. {style}" if style else p for p in prompts]


def prompts_from_file(path: str, style: str) -> list[str]:
    with open(path, encoding="utf-8") as handle:
        content = handle.read()

    if path.lower().endswith(".json"):
        data = json.loads(content)
        # accepts either a bare array or the prompts.json this script writes
        items = data.get("prompts", data) if isinstance(data, dict) else data
        prompts = [str(item).strip() for item in items if str(item).strip()]
        # prompts.json already has the style baked in
        return prompts

    prompts = [line.strip() for line in content.splitlines() if line.strip()]
    return [f"{p.rstrip('. ')}. {style}" if style else p for p in prompts]


# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render a video's image set on the local GPU via ComfyUI.",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--topic", help="Topic; the LLM writes the shot prompts.")
    source.add_argument("--prompts-file", help="Prompts, one per line (.txt) or a JSON array.")

    parser.add_argument("-n", "--count", type=int, default=6,
                        help="Number of images when using --topic (default 6).")
    parser.add_argument("-o", "--output", default="",
                        help="Output folder (default .mp/images/<timestamp>).")
    parser.add_argument("--style", default=DEFAULT_STYLE,
                        help="Style suffix appended to every prompt. Pass '' to disable.")
    parser.add_argument("--size", default="",
                        help="Canvas WxH (default: short_render_size from config.json).")
    parser.add_argument("--seed", type=int, default=None,
                        help="Base seed; image i uses seed+i. Omit for random.")
    parser.add_argument("--only", type=int, default=None,
                        help="Render just this 1-based index from the prompt list.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show the resolved prompts and exit without rendering.")
    args = parser.parse_args()

    if args.size:
        try:
            width, height = (int(part) for part in args.size.lower().split("x", 1))
        except ValueError:
            print(f"error: --size must look like 1080x1920, got '{args.size}'")
            return 2
    else:
        width, height = get_short_render_size()

    # Fail on a dead server before spending time in the LLM.
    if not args.dry_run:
        try:
            ping()
        except ComfyUIError as exc:
            print(f"error: {exc}")
            return 1
        if not list_checkpoints():
            print("warning: ComfyUI reports no checkpoints; the render will likely fail.")

    try:
        if args.topic:
            prompts = prompts_from_topic(args.topic, args.count, args.style)
        else:
            prompts = prompts_from_file(args.prompts_file, args.style)
    except Exception as exc:
        print(f"error: could not build the prompt list: {exc}")
        return 1

    if not prompts:
        print("error: ended up with zero prompts.")
        return 1

    selected = list(enumerate(prompts, start=1))
    if args.only is not None:
        if not 1 <= args.only <= len(prompts):
            print(f"error: --only {args.only} is outside 1..{len(prompts)}")
            return 2
        selected = [selected[args.only - 1]]

    if args.dry_run:
        for index, prompt in selected:
            print(f"\n[{index}] {prompt}")
        return 0

    out_dir = args.output or os.path.join(
        ROOT_DIR, ".mp", "images", datetime.now().strftime("%Y%m%d-%H%M%S")
    )
    os.makedirs(out_dir, exist_ok=True)

    with open(os.path.join(out_dir, "prompts.json"), "w", encoding="utf-8") as handle:
        json.dump(
            {"topic": args.topic or args.prompts_file,
             "size": [width, height],
             "seed": args.seed,
             "prompts": prompts},
            handle, indent=2, ensure_ascii=False,
        )

    print(f"\nRendering {len(selected)} image(s) at {width}x{height} -> {out_dir}\n")

    failures = 0
    for index, prompt in selected:
        seed = None if args.seed is None else args.seed + index
        label = f"[{index}/{len(prompts)}]"
        print(f"{label} {prompt[:88]}{'...' if len(prompt) > 88 else ''}")

        started = time.time()
        try:
            image_bytes = generate_image_bytes_with_comfyui(
                prompt, width=width, height=height, seed=seed
            )
        except ComfyUIError as exc:
            print(f"{label} FAILED: {exc}\n")
            failures += 1
            continue
        except KeyboardInterrupt:
            print("\nInterrupted.")
            return 130

        destination = os.path.join(out_dir, f"{index:02d}.png")
        with open(destination, "wb") as handle:
            handle.write(image_bytes)
        print(f"{label} -> {os.path.basename(destination)} "
              f"({len(image_bytes) / 1024:.0f} KB, {time.time() - started:.1f}s)\n")

    rendered = len(selected) - failures
    print(f"Done: {rendered}/{len(selected)} rendered in {out_dir}")
    if failures:
        print(f"{failures} failed. Re-roll one with: "
              f"--prompts-file {os.path.join(out_dir, 'prompts.json')} --only <n>")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
