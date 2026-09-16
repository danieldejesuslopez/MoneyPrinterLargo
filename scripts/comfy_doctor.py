#!/usr/bin/env python3
"""
Inspect this machine's local image-generation setup and report what is missing.

Run it on the PC that owns the GPU:

    python scripts/comfy_doctor.py

It reports the graphics card, whether PyTorch can actually reach it, whether
ComfyUI is answering, which checkpoints and samplers exist, and whether
config.json points at something real. Nothing is modified.
"""

import json
import os
import platform
import subprocess
import sys

import requests

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT_DIR, "config.json")

_PROBLEMS: list[str] = []


def ok(msg: str) -> None:
    print(f"[ OK ] {msg}")


def warn(msg: str) -> None:
    print(f"[WARN] {msg}")


def fail(msg: str) -> None:
    print(f"[FAIL] {msg}")
    _PROBLEMS.append(msg)


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def load_config() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as handle:
            return json.load(handle)
    except Exception as exc:
        fail(f"Could not read config.json: {exc}")
        return {}


# ---------------------------------------------------------------------------


def report_gpu() -> None:
    section("Graphics card")
    print(f"OS: {platform.system()} {platform.release()} ({platform.machine()})")

    found = False
    if platform.system() == "Windows":
        # Win32_VideoController is the least fragile way to name the card
        # without assuming a vendor tool is installed.
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_VideoController | "
                 "Select-Object -ExpandProperty Name"],
                capture_output=True, text=True, timeout=30,
            )
            for line in result.stdout.splitlines():
                if line.strip():
                    ok(f"GPU: {line.strip()}")
                    found = True
        except Exception as exc:
            warn(f"Could not query Windows for the GPU: {exc}")
    else:
        try:
            result = subprocess.run(["lspci"], capture_output=True, text=True, timeout=30)
            for line in result.stdout.splitlines():
                if "VGA" in line or "Display" in line:
                    ok(f"GPU: {line.split(':', 2)[-1].strip()}")
                    found = True
        except Exception:
            pass

    if not found:
        warn("Could not identify the GPU automatically.")


def report_torch() -> None:
    section("PyTorch backend")
    try:
        import torch
    except ImportError:
        warn("PyTorch is not installed in *this* interpreter. That is fine if "
             "ComfyUI runs in its own venv -- the checks below are what matter.")
        return

    print(f"torch {torch.__version__}")
    hip = getattr(torch.version, "hip", None)
    cuda = getattr(torch.version, "cuda", None)
    if hip:
        ok(f"ROCm/HIP build: {hip}")
    elif cuda:
        ok(f"CUDA build: {cuda}")
    else:
        warn("CPU-only build of PyTorch -- generation would be extremely slow.")

    try:
        if torch.cuda.is_available():
            # On a ROCm build torch.cuda is the HIP shim, so this covers AMD too.
            name = torch.cuda.get_device_name(0)
            total = torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
            ok(f"Device visible to torch: {name} ({total:.1f} GB VRAM)")
            props = torch.cuda.get_device_properties(0)
            arch = getattr(props, "gcnArchName", "")
            if arch:
                print(f"      arch: {arch}")
        else:
            fail("torch cannot see any GPU. Renders would fall back to CPU.")
    except Exception as exc:
        warn(f"Device probe failed: {exc}")


def report_comfyui(config: dict) -> dict:
    section("ComfyUI server")
    base_url = str(config.get("comfyui_base_url") or "http://127.0.0.1:8188").rstrip("/")
    print(f"URL: {base_url}")

    try:
        stats = requests.get(f"{base_url}/system_stats", timeout=8).json()
    except Exception as exc:
        fail(f"ComfyUI is not answering. Start it, then re-run. Detail: {exc}")
        return {}

    system = stats.get("system") or {}
    ok(f"ComfyUI is up (python {system.get('python_version', '?').split()[0]})")

    for device in stats.get("devices") or []:
        vram_total = device.get("vram_total", 0) / (1024 ** 3)
        vram_free = device.get("vram_free", 0) / (1024 ** 3)
        ok(f"Compute device: {device.get('name', '?')} "
           f"[{device.get('type', '?')}] {vram_free:.1f}/{vram_total:.1f} GB free")
        if str(device.get("type", "")).lower() == "cpu":
            fail("ComfyUI is running on the CPU, not the graphics card.")

    return {"base_url": base_url}


def report_models(base_url: str, config: dict) -> None:
    section("Checkpoints and samplers")
    try:
        info = requests.get(f"{base_url}/object_info/CheckpointLoaderSimple", timeout=15).json()
        checkpoints = info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0]
    except Exception as exc:
        warn(f"Could not list checkpoints: {exc}")
        return

    if not checkpoints:
        fail("ComfyUI has no checkpoints. Drop a .safetensors into "
             "ComfyUI/models/checkpoints and restart it.")
    else:
        ok(f"{len(checkpoints)} checkpoint(s) available:")
        for name in checkpoints:
            print(f"        - {name}")

    configured = str(config.get("comfyui_checkpoint") or "").strip()
    workflow = str(config.get("comfyui_workflow") or "").strip()

    if workflow:
        path = workflow if os.path.isabs(workflow) else os.path.join(ROOT_DIR, workflow)
        if os.path.isfile(path):
            ok(f"Custom workflow: {path}")
        else:
            fail(f"comfyui_workflow points at a missing file: {path}")
    elif not configured:
        fail("comfyui_checkpoint is empty in config.json. Set it to one of the "
             "names listed above.")
    elif configured not in checkpoints:
        fail(f"comfyui_checkpoint '{configured}' is not in ComfyUI's list above.")
    else:
        ok(f"comfyui_checkpoint '{configured}' resolves correctly.")

    try:
        ks = requests.get(f"{base_url}/object_info/KSampler", timeout=15).json()
        required = ks["KSampler"]["input"]["required"]
        samplers = required["sampler_name"][0]
        schedulers = required["scheduler"][0]
    except Exception:
        return

    for key, values in (("comfyui_sampler", samplers), ("comfyui_scheduler", schedulers)):
        chosen = str(config.get(key) or "").strip()
        default = "dpmpp_2m" if key.endswith("sampler") else "karras"
        chosen = chosen or default
        if chosen in values:
            ok(f"{key} '{chosen}' is valid.")
        else:
            fail(f"{key} '{chosen}' is not supported. Options: {', '.join(values[:12])}...")


def report_provider(config: dict) -> None:
    section("MoneyPrinter wiring")
    provider = str(config.get("image_provider") or "auto").strip().lower()
    if provider == "comfyui":
        ok("image_provider is 'comfyui' -- the local GPU will be used.")
    else:
        fail(f"image_provider is '{provider}'. Set it to 'comfyui' in config.json "
             "to render locally.")

    render_size = str(config.get("short_render_size") or "")
    max_side = config.get("comfyui_native_max_side", 1216)
    if render_size:
        print(f"      Shorts canvas {render_size}, rendered natively at "
              f"max side {max_side} then upscaled.")


def main() -> int:
    print("MoneyPrinterLargo - local GPU image generation doctor")
    config = load_config()
    report_gpu()
    report_torch()
    comfy = report_comfyui(config)
    if comfy:
        report_models(comfy["base_url"], config)
    report_provider(config)

    section("Summary")
    if _PROBLEMS:
        print(f"{len(_PROBLEMS)} thing(s) to fix:")
        for item in _PROBLEMS:
            print(f"  - {item}")
        return 1

    ok("Everything checks out. Try: python scripts/generate_video_images.py --help")
    return 0


if __name__ == "__main__":
    sys.exit(main())
