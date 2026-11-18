"""Paths and tunables for the clips module.

Follows the project convention: every getter re-reads `config.json`, so a
change takes effect on the next call without a restart. Everything lives
under a single `clips` object in the config file, and every key has a
working default — the module runs with no configuration at all.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


#: Where finished clips live. Deliberately a *subdirectory* of `.mp/`:
#: `utils.rem_temp_files()` sweeps files directly inside `.mp/` and
#: everything under `.mp/tmp/`, but does not recurse into other
#: subdirectories, so rendered clips survive a generation run.
CLIPS_DIRNAME = "clips"

#: Downloaded source videos. Isolated per source id, and never mixed with
#: the code or the outputs: a download is untrusted input.
SOURCES_DIRNAME = "sources"

TREND_PROFILE_FILE = "trend_profile.json"
DECISIONS_FILE = "decisions.jsonl"

DEFAULTS = {
    # Clip length bounds, in seconds. The trend profile narrows this to what
    # actually wins on the reference channels; these are only the fallback.
    "min_seconds": 18.0,
    "max_seconds": 60.0,
    # How many clips to keep from one source by default.
    "count": 3,
    # Two kept clips may not overlap by more than this share of their length.
    "max_overlap": 0.25,
    # Reframe strategy for a 16:9 source: "blur" (full frame over a blurred
    # fill) or "crop" (centre cut, loses the sides).
    "reframe": "blur",
    # Add a top panel only when the main crop cuts or excludes a face.
    # A face already fully visible in the video is never duplicated.
    "auto_facecam": True,
    # Streamer camera box in source pixels, "x,y,w,h", used by the
    # "facecam" layout (camera on top, gameplay below). Per-source in
    # practice, so usually passed on the command line instead.
    "facecam": "",
    "width": 1080,
    "height": 1920,
    "fps": 30,
    # Poppins Black is what the rest of the project burns its subtitles
    # with (`YouTube.py`), so a clip matches the channel's own look.
    "font": "Poppins-Black.ttf",
    "subtitle_font_size": 80,
    # Burn the clip's title on screen as hook text: "auto" only when the
    # clip has no subtitles (no speech, or subtitles turned off), which is
    # when the frame otherwise has nothing to read; "always"; "never".
    "title_overlay": "auto",
    "title_font_size": 88,
    # Transcription language ("es", "en"...). Empty lets Whisper detect it,
    # which the small models get wrong on narrated video with music under
    # it — set it when you know what your sources speak.
    "language": "",
    # Captions fetched with a download, used instead of Whisper for the
    # subtitles and the cut points: "auto" takes a person-written track,
    # else the platform's speech recognition in the original language;
    # "manual" takes only person-written ones; "off" always uses Whisper.
    "captions": "auto",
    # Longest video we will download without being asked again, in minutes.
    "max_source_minutes": 180,
}


def root_dir() -> str:
    """Project root, derived from this file rather than from `sys.path`.

    `config.ROOT_DIR` is `dirname(sys.path[0])`, which is correct for
    `python src/main.py` and wrong under pytest or any other entry point.
    `src/brain/config.py` resolves its own root for the same reason.
    """
    return str(Path(__file__).resolve().parents[2])


def _config() -> dict:
    try:
        with open(os.path.join(root_dir(), "config.json"), "r", encoding="utf-8") as file:
            raw = json.load(file)
    except (OSError, ValueError):
        return {}
    block = raw.get("clips")
    return block if isinstance(block, dict) else {}


def setting(name: str):
    """One `clips.<name>` setting, falling back to its documented default."""
    value = _config().get(name)
    if value is None or value == "":
        return DEFAULTS.get(name)
    default = DEFAULTS.get(name)
    if isinstance(default, float):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    if isinstance(default, int) and not isinstance(default, bool):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    return value


def clips_dir() -> str:
    path = os.path.join(root_dir(), ".mp", CLIPS_DIRNAME)
    os.makedirs(path, exist_ok=True)
    return path


def clips_file(name: str) -> str:
    return os.path.join(clips_dir(), name)


def sources_dir() -> str:
    """Parent of the per-source download directories."""
    path = os.path.join(clips_dir(), SOURCES_DIRNAME)
    os.makedirs(path, exist_ok=True)
    return path


def source_dir(source_id: str) -> str:
    """A fresh, isolated directory for one downloaded video."""
    path = os.path.join(sources_dir(), source_id)
    os.makedirs(path, exist_ok=True)
    return path


def fonts_dir() -> str:
    return os.path.join(root_dir(), "fonts")


def _is_real_font(path: str) -> bool:
    """Whether a file is actually a font and not a mangled download.

    `fonts/Montserrat-ExtraBold.ttf` in this repo begins with CRLF bytes
    instead of the TrueType magic, so it exists, is 300 KB, and cannot be
    loaded. Checking the magic number is cheaper than discovering that
    inside libass, where the only symptom is subtitles silently rendered
    in a fallback face.
    """
    try:
        with open(path, "rb") as file:
            magic = file.read(4)
    except OSError:
        return False
    # TrueType, OpenType/CFF, TrueType collection, and the two web wrappers.
    return magic in (
        b"\x00\x01\x00\x00", b"OTTO", b"true", b"ttcf", b"wOFF", b"wOF2"
    )


def font_path() -> str:
    """The subtitle font, skipping any file that is not a loadable font."""
    directory = fonts_dir()
    candidate = os.path.join(directory, str(setting("font")))
    if os.path.isfile(candidate) and _is_real_font(candidate):
        return candidate
    try:
        for name in sorted(os.listdir(directory)):
            if not name.lower().endswith((".ttf", ".otf", ".ttc")):
                continue
            full = os.path.join(directory, name)
            if _is_real_font(full):
                return full
    except OSError:
        pass
    return ""
