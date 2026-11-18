"""Original brand assets and editorial cards; no simulated gameplay imagery."""

from __future__ import annotations

from pathlib import Path
import os
import uuid
from PIL import Image, ImageDraw, ImageFont

from .store import ROOT

INK, PINK, PEACH, BLUE = "#192d46", "#f3a6bd", "#f5ca94", "#8bc4cf"


def _save(image: Image.Image, path: Path):
    temporary = path.with_name(path.stem + "." + uuid.uuid4().hex + ".png")
    try:
        image.save(temporary)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def font(size: int):
    from clips.config import font_path
    path = font_path()
    try:
        return ImageFont.truetype(path or "arial.ttf", size)
    except OSError:
        return ImageFont.load_default(size=size)


def fit_text(draw, text: str, box: tuple[int, int, int, int], maximum: int = 86, fill="white"):
    x, y, width, height = box
    for size in range(maximum, 19, -2):
        face = font(size)
        lines, line = [], ""
        for word in text.split():
            candidate = (line + " " + word).strip()
            if draw.textlength(candidate, font=face) > width and line:
                lines.append(line)
                line = word
            else:
                line = candidate
        if line:
            lines.append(line)
        spacing = int(size * 1.35)
        if len(lines) * spacing <= height and all(draw.textlength(line, font=face) <= width for line in lines):
            for index, line in enumerate(lines):
                draw.text((x, y + index * spacing), line, font=face, fill=fill)
            return
    raise ValueError("El texto es demasiado largo para la tarjeta; resume el hecho antes de generar")


def brand(directory: Path, name: str, handle: str) -> dict[str, str]:
    directory.mkdir(parents=True, exist_ok=True)
    avatar = Image.new("RGB", (800, 800), INK)
    draw = ImageDraw.Draw(avatar)
    draw.ellipse((145, 145, 655, 655), fill=PINK)
    draw.polygon([(220, 540), (400, 235), (580, 540)], fill=INK)
    draw.ellipse((345, 470, 455, 580), fill=PEACH)
    avatar_path = directory / "avatar-youtube.png"
    _save(avatar, avatar_path)
    banner = Image.new("RGB", (2560, 1440), INK)
    draw = ImageDraw.Draw(banner)
    draw.ellipse((1880, -180, 2840, 780), fill="#614762")
    draw.rectangle((0, 1060, 2560, 1440), fill="#14243b")
    # Keep all text in the central strip so the mobile crop remains useful.
    fit_text(draw, name, (560, 555, 1430, 130), 106)
    fit_text(draw, "GTA 6 · Noticias, clips y guías", (565, 700, 1400, 72), 46, PEACH)
    fit_text(draw, f"{handle} · Fan page independiente", (565, 795, 1400, 52), 30, BLUE)
    banner_path = directory / "banner-youtube.png"
    _save(banner, banner_path)
    return {"avatar": str(avatar_path), "banner": str(banner_path)}


def cards(directory: Path, *, name: str, handle: str, title: str, fact: str, source: str) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    result = []
    for index, (heading, body, accent) in enumerate([
        ("GTA 6", title, PINK), ("Información confirmada", fact, PEACH),
        ("¿Qué opinas?", "Comenta tu plan para GTA 6.\nFuente oficial en la descripción.", BLUE),
    ]):
        canvas = Image.new("RGB", (1080, 1920), INK)
        draw = ImageDraw.Draw(canvas)
        draw.rectangle((0, 0, 1080, 22), fill=accent)
        draw.ellipse((720, 60, 1200, 540), fill="#493d5e")
        fit_text(draw, heading, (90, 180, 900, 160), 70, accent)
        fit_text(draw, body, (90, 440, 900, 660), 86)
        fit_text(draw, source[:65], (90, 1150, 900, 95), 25, BLUE)
        draw.line((90, 1590, 990, 1590), fill=accent, width=3)
        fit_text(draw, name, (90, 1640, 900, 90), 48)
        fit_text(draw, f"{handle} · Fan page independiente", (90, 1750, 900, 65), 26, BLUE)
        path = directory / f"card-{index + 1}.png"
        _save(canvas, path)
        result.append(path)
    return result
