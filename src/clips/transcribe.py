"""Word-level transcription of a source video.

Where the cut lands is decided by word timings, so this is the one step
the rest of the module cannot work without. It reuses the Whisper
settings the project already has (`whisper_model`, `whisper_device`,
`whisper_compute_type`) rather than inventing its own.

Transcripts are cached beside the source. A two-hour video costs minutes
of GPU time to transcribe and the result never changes, so a second run
over the same source — a different clip count, a retuned scorer — must not
pay for it again.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Any

from . import config as clips_config


TRANSCRIPT_NAME = "transcript.json"

#: Whisper wants 16 kHz mono; handing it the video directly makes it
#: decode the whole file through its own ffmpeg call anyway.
AUDIO_NAME = "audio.wav"
SAMPLE_RATE = 16000


class TranscribeError(RuntimeError):
    """Transcription could not be produced, with a reason worth printing."""


def _project_setting(key: str, default: Any) -> Any:
    """One top-level `config.json` value, read the way the project does."""
    try:
        with open(
            os.path.join(clips_config.root_dir(), "config.json"), "r", encoding="utf-8"
        ) as file:
            value = json.load(file).get(key)
    except (OSError, ValueError):
        return default
    return default if value in (None, "") else value


def extract_audio(video_path: str, out_dir: str, force: bool = False) -> str:
    """16 kHz mono WAV next to the source, reused when already there."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise TranscribeError("`ffmpeg` is not on PATH")
    target = os.path.join(out_dir, AUDIO_NAME)
    if os.path.isfile(target) and not force and os.path.getsize(target) > 1024:
        return target
    result = subprocess.run(
        [
            ffmpeg, "-y", "-hide_banner", "-nostdin", "-loglevel", "error",
            "-i", video_path,
            "-vn",
            "-ac", "1",
            "-ar", str(SAMPLE_RATE),
            "-c:a", "pcm_s16le",
            target,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=3600,
    )
    if result.returncode != 0 or not os.path.isfile(target):
        tail = (result.stderr or "").strip().splitlines()
        raise TranscribeError(
            "ffmpeg could not extract audio: " + (tail[-1] if tail else "no output")
        )
    return target


_CUE_TIME = re.compile(
    r"(\d+):(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(\d+):(\d{2}):(\d{2})[.,](\d{3})"
)
#: Caption annotations that are not speech: tags, "*laughs*", "(x10)",
#: "[music]", and the bare "x4" some uploaders use for repetition.
_ANNOTATION = re.compile(r"<[^>]+>|\*[^*]*\*|\([^)]*\)|\[[^\]]*\]|(?<!\w)x\d+(?!\w)")


def _seconds(h, m, s, ms) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


#: An inline word timestamp in YouTube's generated captions:
#: `word<00:00:01.120><c> next</c>`.
_INLINE_TIME = re.compile(r"<(\d+):(\d{2}):(\d{2})[.,](\d{3})>")

#: Generated captions repeat each line in a ~10 ms "hold" cue before the
#: next one; anything this short is a repeat, not speech.
_HOLD_CUE_SECONDS = 0.05


def _clean_tokens(text: str) -> list[str]:
    text = _ANNOTATION.sub(" ", text)
    return [t for t in text.split() if any(c.isalnum() for c in t)]


def _read_cues(lines: list[str]) -> list[tuple[float, float, list[str]]]:
    """(start, end, body lines) per cue.

    A cue body runs until a truly empty line. Generated captions open
    each body with a line that is a single space, so "blank after
    stripping" cannot be the terminator — that would read every cue of a
    generated track as empty.
    """
    cues = []
    index = 0
    while index < len(lines):
        match = _CUE_TIME.search(lines[index])
        index += 1
        if not match:
            continue
        start = _seconds(*match.groups()[:4])
        end = _seconds(*match.groups()[4:])
        # That leading single-space line is lost by anything that trims
        # trailing whitespace (editors, copy-paste, some converters), which
        # leaves an empty line where the body starts. Step over it when
        # what follows is text rather than the next cue's identifier.
        if (
            index + 1 < len(lines)
            and lines[index] == ""
            and lines[index + 1] != ""
            and not _CUE_TIME.search(lines[index + 1])
            and not (index + 2 < len(lines) and _CUE_TIME.search(lines[index + 2]))
        ):
            index += 1
        body = []
        while (
            index < len(lines)
            and lines[index] != ""
            and not _CUE_TIME.search(lines[index])
        ):
            body.append(lines[index])
            index += 1
        cues.append((start, end, body))
    return cues


def _timed_line_words(line: str, cue_start: float, cue_end: float) -> list[dict[str, Any]]:
    """Words of one generated-caption line, each at its own timestamp.

    The text before the first inline stamp starts at the cue's start;
    every later chunk starts at the stamp in front of it. A word ends
    where the next one begins, and the last one at the end of the cue.
    """
    starts = [cue_start]
    chunks = []
    position = 0
    for match in _INLINE_TIME.finditer(line):
        chunks.append(line[position:match.start()])
        starts.append(_seconds(*match.groups()))
        position = match.end()
    chunks.append(line[position:])

    timed: list[tuple[float, str]] = []
    for chunk_start, chunk in zip(starts, chunks):
        for token in _clean_tokens(chunk):
            timed.append((chunk_start, token))

    words = []
    for index, (word_start, token) in enumerate(timed):
        word_end = timed[index + 1][0] if index + 1 < len(timed) else cue_end
        if word_end <= word_start:
            word_end = word_start + 0.05
        words.append(
            {"start": round(word_start, 3), "end": round(word_end, 3), "word": token}
        )
    return words


def words_from_captions(path: str) -> list[dict[str, Any]]:
    """Timed words from a WebVTT or SRT caption file.

    Two shapes are handled:

      - **Person-written** captions are timed per cue, so each cue's
        words are spread evenly across it — the same fallback this module
        uses when Whisper returns no word timings.
      - **Generated** captions (YouTube's speech recognition) roll: each
        cue repeats the previous line and adds a new one carrying a
        timestamp per word, followed by a ~10 ms hold cue that repeats it
        again. Only the newest line of each real cue is read, and its
        words keep their real timings, which are better than anything
        spread evenly.
    """
    with open(path, "r", encoding="utf-8-sig", errors="replace") as file:
        lines = file.read().splitlines()

    cues = _read_cues(lines)
    generated = any(_INLINE_TIME.search(line) for _, _, body in cues for line in body)

    words: list[dict[str, Any]] = []
    for start, end, body in cues:
        if end <= start:
            continue
        if generated:
            if end - start < _HOLD_CUE_SECONDS:
                continue
            content = [line for line in body if line.strip()]
            if not content:
                continue
            words.extend(_timed_line_words(content[-1], start, end))
            continue
        tokens = _clean_tokens(" ".join(line.strip() for line in body))
        if not tokens:
            continue
        step = (end - start) / len(tokens)
        for position, token in enumerate(tokens):
            words.append(
                {
                    "start": round(start + position * step, 3),
                    "end": round(start + (position + 1) * step, 3),
                    "word": token,
                }
            )
    words.sort(key=lambda w: w["start"])
    return words


def _flatten(segments) -> tuple[list[dict[str, Any]], str]:
    """Whisper segments to flat timed words, plus the plain text.

    Falls back to spreading a segment's words evenly across its span when
    the model returns no per-word timing, which is what the existing
    karaoke path in `YouTube.py` does for the same reason.
    """
    words: list[dict[str, Any]] = []
    texts: list[str] = []
    for segment in segments:
        text = str(getattr(segment, "text", "") or "").strip()
        if text:
            texts.append(text)
        segment_words = getattr(segment, "words", None) or []
        if segment_words:
            for item in segment_words:
                token = str(getattr(item, "word", "")).strip()
                if not token:
                    continue
                start = float(getattr(item, "start", 0.0) or 0.0)
                end = float(getattr(item, "end", start + 0.05) or start + 0.05)
                words.append(
                    {
                        "start": max(0.0, start),
                        "end": max(start + 0.05, end),
                        "word": token,
                    }
                )
            continue
        tokens = text.split()
        if not tokens:
            continue
        start = float(getattr(segment, "start", 0.0) or 0.0)
        end = float(getattr(segment, "end", start) or start)
        step = max(0.05, (end - start) / len(tokens))
        for index, token in enumerate(tokens):
            words.append(
                {
                    "start": start + index * step,
                    "end": start + (index + 1) * step,
                    "word": token,
                }
            )
    return words, " ".join(texts).strip()


def transcribe(
    video_path: str,
    out_dir: str,
    language: str = "",
    refresh: bool = False,
    on_status=None,
    task: str = "transcribe",
    model_name: str = "",
) -> dict[str, Any]:
    """Timed words for a video, cached in `out_dir/transcript.json`."""
    if task not in ("transcribe", "translate"):
        raise TranscribeError("Whisper task must be transcribe or translate")
    model_name = model_name or str(_project_setting("whisper_model", "base"))
    cache_path = os.path.join(out_dir, TRANSCRIPT_NAME if task == "transcribe" else "transcript.en.json")
    if not refresh and os.path.isfile(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as file:
                cached = json.load(file)
            # A transcript made in another language is not a cache hit: a
            # rerun with `--language es` after a wrong auto-detect must
            # actually re-transcribe.
            if (
                isinstance(cached, dict)
                and cached.get("words")
                and cached.get("task", "transcribe") == task
                and cached.get("model") == model_name
                and (not language or cached.get("requested_language") == language)
            ):
                return cached
        except (OSError, ValueError):
            pass

    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise TranscribeError(
            "faster-whisper is not installed; it is in requirements.txt "
            "(`pip install faster-whisper`)"
        ) from exc

    audio_path = extract_audio(video_path, out_dir)
    if on_status:
        on_status(f"loading Whisper '{model_name}'")

    try:
        model = WhisperModel(
            model_name,
            device=str(_project_setting("whisper_device", "auto")),
            compute_type=str(_project_setting("whisper_compute_type", "int8")),
        )
    except Exception as exc:  # pragma: no cover - environment dependent
        raise TranscribeError(f"could not load Whisper model '{model_name}': {exc}") from exc

    if on_status:
        on_status("transcribing (this is the slow step)")
    try:
        segments, info = model.transcribe(
            audio_path,
            vad_filter=True,
            word_timestamps=True,
            language=(language or None),
            task=task,
        )
        words, text = _flatten(segments)
    except Exception as exc:  # pragma: no cover - environment dependent
        raise TranscribeError(f"Whisper failed: {exc}") from exc

    if not words:
        raise TranscribeError(
            "Whisper produced no words. If the source has no speech there is "
            "nothing to clip on."
        )

    payload = {
        "model": model_name,
        "requested_language": language or "",
        "language": "en" if task == "translate" else str(getattr(info, "language", "") or language or ""),
        "source_language": str(getattr(info, "language", "") or language or ""),
        "task": task,
        "duration": float(getattr(info, "duration", 0.0) or 0.0),
        "word_count": len(words),
        "text": text,
        "words": words,
    }
    tmp = cache_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False)
    os.replace(tmp, cache_path)
    return payload
