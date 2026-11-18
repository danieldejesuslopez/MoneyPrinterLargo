"""Clipping a stream that is still live: what has aired so far.

A live stream has no end, so the normal "download the video" step never
finishes, and yt-dlp refuses to download *sections* of a stream from its
start ("This format cannot be partially downloaded"). But YouTube serves a
live stream as numbered DASH fragments (`&sq=N`, about five seconds each)
from the very first one, each independently decodable, and any of them
can be fetched on its own in a fraction of a second.

That makes a cheap plan possible:

  1. fetch only the **audio** of everything aired so far (~1 MB a minute),
     in parallel, and treat that as the source to transcribe and score;
  2. once the pipeline has chosen its spans, fetch the **video** fragments
     covering just those spans, and mux each into a small piece to render.

A two-hour stream costs ~130 MB of audio plus a few MB of video per clip,
instead of gigabytes of video nobody will watch.

Times: the audio file's 0 is the first fragment fetched, whose media time
is recorded as `start_time`. A span chosen on the transcript is in that
timeline; a piece's own 0 is its first fragment, so a span's position in a
piece is `span - (piece_start_time - audio_start_time)`.
"""

from __future__ import annotations

import concurrent.futures
import json
import math
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable

#: Fragment length when the format does not say. YouTube's live default.
DEFAULT_TARGET_SECONDS = 5.0

#: Parallel fragment fetches. Enough to saturate a home connection on the
#: small audio fragments without looking like a flood.
WORKERS = 8

#: Attempts per fragment before the run gives up on it.
RETRIES = 3

#: Highest video height fetched for clip pieces. The clip is 1080x1920 and
#: the 16:9 frame is scaled to 1080 wide, so 720p is already more than the
#: blurred layout uses, at half the bytes of 1080p.
MAX_VIDEO_HEIGHT = 720


class LiveError(RuntimeError):
    """A live stream could not be captured, with a reason worth printing."""


class LiveAuthError(LiveError):
    """The signed fragment URL stopped being accepted (HTTP 401/403).

    YouTube revokes a stream's signed URLs part-way through a long fetch.
    Retrying the same URL never helps; resolving fresh ones does.
    """


def _yt_dlp() -> str:
    found = shutil.which("yt-dlp")
    if not found:
        raise LiveError("`yt-dlp` is not on PATH")
    return found


def live_formats(url: str) -> dict[str, Any]:
    """Fragment URLs for the stream's audio and a ≤720p video, from its start.

    Format URLs expire after a few hours, so this is called again whenever
    fragments are needed rather than stored in the manifest.
    """
    result = subprocess.run(
        [_yt_dlp(), "--live-from-start", "--no-warnings", "--no-playlist", "-j", url],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()
        raise LiveError("yt-dlp could not read the stream: " + (tail[-1] if tail else "no output"))
    try:
        info = json.loads(result.stdout)
    except ValueError as exc:
        raise LiveError(f"unreadable stream metadata: {exc}") from exc
    return pick_formats(info)


def pick_formats(info: dict[str, Any]) -> dict[str, Any]:
    """The audio and video formats to fetch fragments from. Pure."""
    formats = [
        f for f in info.get("formats") or []
        if isinstance(f, dict) and f.get("url") and "dash" in str(f.get("protocol") or "")
    ]
    audio = [
        f for f in formats
        if f.get("vcodec") in (None, "none") and f.get("acodec") not in (None, "none")
    ]
    video = [
        f for f in formats
        if f.get("vcodec") not in (None, "none") and f.get("ext") == "mp4"
    ]
    if not audio or not video:
        raise LiveError(
            "this stream does not offer separate audio and video fragments from "
            "its start (it may have DVR disabled)"
        )
    # m4a over webm, then the better bitrate.
    audio.sort(key=lambda f: (f.get("ext") == "m4a", f.get("abr") or 0), reverse=True)
    fitting = [f for f in video if (f.get("height") or 0) <= MAX_VIDEO_HEIGHT]
    pool = fitting or video
    pool.sort(key=lambda f: ((f.get("height") or 0), -(f.get("tbr") or 0)), reverse=True)

    def pack(fmt: dict[str, Any]) -> dict[str, Any]:
        return {
            "format_id": str(fmt.get("format_id") or ""),
            "url": fmt["url"],
            "headers": dict(fmt.get("http_headers") or {}),
            "height": fmt.get("height"),
        }

    target = audio[0].get("target_duration") or video[0].get("target_duration")
    return {
        "title": str(info.get("title") or ""),
        "uploader": str(info.get("uploader") or info.get("channel") or ""),
        "language": str(info.get("language") or ""),
        "is_live": bool(info.get("is_live")),
        "target": float(target or DEFAULT_TARGET_SECONDS),
        "audio": pack(audio[0]),
        "video": pack(pool[0]),
    }


def _get(fmt: dict[str, Any], sq: int) -> tuple[bytes, dict[str, str]]:
    request = urllib.request.Request(f"{fmt['url']}&sq={sq}", headers=fmt.get("headers") or {})
    last: Exception | None = None
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read(), dict(response.headers)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise LiveAuthError(f"fragment {sq}: HTTP {exc.code}") from exc
            last = exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = exc
        time.sleep(0.5 * (attempt + 1))
    raise LiveError(f"fragment {sq} could not be fetched: {last}")


def head_sequence(fmt: dict[str, Any]) -> int:
    """The newest fragment number the stream has published."""
    _, headers = _get(fmt, 0)
    lowered = {k.lower(): v for k, v in headers.items()}
    try:
        return int(lowered["x-head-seqnum"])
    except (KeyError, ValueError) as exc:
        raise LiveError("the stream did not report how far it has aired") from exc


def sequence_range(
    head: int, target: float, max_minutes: float | None = None
) -> tuple[int, int]:
    """Fragments to fetch: all of them, or the newest `max_minutes`. Pure."""
    first = 0
    if max_minutes and max_minutes > 0:
        keep = int(math.ceil(max_minutes * 60 / target))
        first = max(0, head - keep + 1)
    return first, head


def span_sequences(
    start: float, end: float, target: float, first: int, last: int
) -> tuple[int, int]:
    """Fragments covering [start, end] of a timeline that begins at `first`.

    One fragment of margin each side: fragment boundaries drift a little
    from exact multiples of `target`, and a span that starts a few frames
    before its first fragment would begin on a black frame. Pure.
    """
    lo = first + int(math.floor(start / target)) - 1
    hi = first + int(math.ceil(end / target)) + 1
    return max(first, lo), min(last, hi)


def fetch_range(
    fmt: dict[str, Any],
    first: int,
    last: int,
    out_path: str,
    on_progress: Callable[[int, int], None] | None = None,
    cache_dir: str | None = None,
    refresh: Callable[[], dict[str, Any]] | None = None,
) -> str:
    """Fetch fragments `first..last` in parallel and join them in order.

    Each fragment is a self-contained fMP4 piece, so joining is a plain
    byte concatenation — the same thing yt-dlp does for these streams.

    `cache_dir` keeps fragments between runs. An aired fragment never
    changes, so a later run on the same stream fetches only what is new
    and a run that failed half-way resumes instead of starting over.
    `refresh` resolves fresh signed URLs when the current ones are revoked.
    """
    total = last - first + 1
    if total <= 0:
        raise LiveError("nothing to fetch")
    work_dir = cache_dir or out_path + ".frags"
    os.makedirs(work_dir, exist_ok=True)

    state = {"fmt": fmt}
    lock = threading.Lock()

    def one(sq: int) -> int:
        target = os.path.join(work_dir, f"{sq:08d}")
        if os.path.isfile(target) and os.path.getsize(target) > 0:
            return sq
        for attempt in range(RETRIES):
            current = state["fmt"]
            try:
                data, _ = _get(current, sq)
                break
            except LiveAuthError:
                if refresh is None or attempt == RETRIES - 1:
                    raise
                with lock:
                    # Only the first thread to notice refreshes; the rest
                    # pick up the new URLs it stored.
                    if state["fmt"] is current:
                        state["fmt"] = refresh()
        with open(target + ".tmp", "wb") as file:
            file.write(data)
        os.replace(target + ".tmp", target)
        return sq

    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for _ in pool.map(one, range(first, last + 1)):
            done += 1
            if on_progress and (done % 100 == 0 or done == total):
                on_progress(done, total)

    with open(out_path + ".tmp", "wb") as joined:
        for sq in range(first, last + 1):
            with open(os.path.join(work_dir, f"{sq:08d}"), "rb") as part:
                shutil.copyfileobj(part, joined)
    os.replace(out_path + ".tmp", out_path)
    if not cache_dir:
        shutil.rmtree(work_dir, ignore_errors=True)
    return out_path


def probe_start(path: str) -> float:
    """Media start time of a file, which for joined fragments is the stream
    time of its first fragment."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0.0
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=start_time",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, timeout=60, check=False,
    )
    try:
        return float((result.stdout or "0").strip())
    except ValueError:
        return 0.0


def remux(inputs: list[str], out_path: str, maps: list[str]) -> str:
    """Copy streams into a clean MP4 starting at 0 (no re-encode)."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise LiveError("`ffmpeg` is not on PATH")
    command = [ffmpeg, "-y", "-hide_banner", "-nostdin", "-loglevel", "error"]
    for path in inputs:
        command += ["-i", path]
    for stream in maps:
        command += ["-map", stream]
    command += ["-c", "copy", "-movflags", "+faststart", out_path]
    result = subprocess.run(command, capture_output=True, text=True, timeout=1800, check=False)
    if result.returncode != 0 or not os.path.isfile(out_path):
        tail = (result.stderr or "").strip().splitlines()
        raise LiveError("ffmpeg could not join the fragments: " + (tail[-1] if tail else "no output"))
    return out_path
