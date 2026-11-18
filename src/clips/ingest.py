"""Getting a source video onto disk, and recording where it came from.

A clip is a derivative work, so the provenance of its source is part of
the output, not an afterthought. Every download writes a manifest naming
the URL, the uploader and the moment it was fetched, and carrying the
rights acknowledgement the caller made when they asked for it. If a clip
later has to be taken down, the manifest says what it was cut from.

Downloads land in their own directory per source
(`.mp/clips/sources/<id>/`) and are never mixed with code or with the
rendered output: a downloaded file is untrusted input.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

from . import config as clips_config


MANIFEST_NAME = "source.json"

#: Container we ask yt-dlp for. mp4 keeps ffmpeg's seek fast and avoids a
#: remux step before cutting.
FORMAT_SPEC = "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"

VIDEO_EXTENSIONS = (".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi")


class IngestError(RuntimeError):
    """A source could not be obtained, with a reason worth printing."""


@dataclass
class Source:
    """A video on disk, plus everything known about where it came from."""

    id: str
    path: str
    title: str = ""
    url: str = ""
    uploader: str = ""
    duration_seconds: float = 0.0
    language: str = ""
    origin: str = "local"
    fetched_at: str = ""
    rights_note: str = ""
    #: Captions fetched alongside the video, if the platform had usable
    #: ones. `captions_kind` is "manual" (written by a person) or "auto"
    #: (the platform's own speech recognition, original language only).
    captions_path: str = ""
    captions_kind: str = ""
    captions_language: str = ""
    #: Whether captions were looked for at all, so a manifest written
    #: before this existed gets one lookup instead of one per run.
    captions_checked: bool = False
    #: A snapshot of a stream that was still live: `path` is the *audio* of
    #: what had aired, and video is fetched per clip by `materialize_span`.
    #: `live_start_time` is the stream time of the audio's first fragment.
    live: bool = False
    live_first_sq: int = 0
    live_last_sq: int = 0
    live_target: float = 0.0
    live_start_time: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def directory(self) -> str:
        return os.path.dirname(self.path)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "path": self.path,
            "title": self.title,
            "url": self.url,
            "uploader": self.uploader,
            "duration_seconds": self.duration_seconds,
            "language": self.language,
            "origin": self.origin,
            "fetched_at": self.fetched_at,
            "rights_note": self.rights_note,
            "captions_path": self.captions_path,
            "captions_kind": self.captions_kind,
            "captions_language": self.captions_language,
            "captions_checked": self.captions_checked,
            "live": self.live,
            "live_first_sq": self.live_first_sq,
            "live_last_sq": self.live_last_sq,
            "live_target": self.live_target,
            "live_start_time": self.live_start_time,
        }


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


#: Query parameters that say where to start watching, or who shared the
#: link, rather than which video it is. "Copy link at current time" adds
#: `t=`, the share sheet adds `si=`; hashing those made the same video a
#: second source, so a link copied twice downloaded 285 MB twice and paid
#: for Whisper twice over a transcript already on disk.
_NON_IDENTIFYING_PARAMS = frozenset({
    "t", "start", "end", "time_continue", "si", "feature", "app", "pp",
    "ab_channel", "themerefresh", "index",
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "igshid",
})

#: The 11-character video id in the short share form `youtu.be/<id>`.
_YOUTUBE_SHORT_PATH = re.compile(r"^/([A-Za-z0-9_-]{11})/?$")

#: YouTube spellings that reach the same watch page. The address-bar host
#: is the canonical one because it is what the sources already on disk are
#: named after, so normalising onto it reuses them instead of orphaning
#: them behind a new name.
_YOUTUBE_HOSTS = {"youtube.com", "m.youtube.com", "www.youtube.com"}


def canonical_url(url: str) -> str:
    """One spelling per video, for naming its directory.

    Only the parts that cannot change *which* video a link points at are
    dropped, so a host this does not recognise keeps every parameter it
    arrived with. This is the cache key alone: provenance stays in the
    manifest, which records the URL the run that downloaded it was given.
    """
    url = (url or "").strip()
    parts = urllib.parse.urlsplit(url)
    if not parts.scheme or not parts.netloc:
        # Nothing to take apart — hash it the way it came.
        return url

    host = parts.netloc.lower()
    path = parts.path
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    short = _YOUTUBE_SHORT_PATH.match(path) if host == "youtu.be" else None
    if short or host in _YOUTUBE_HOSTS:
        # Only drop parameters on the hosts whose URL grammar we actually
        # know. Anywhere else `start` or `index` may be exactly what picks
        # the resource, and collapsing two distinct links onto one cache key
        # would hand back the wrong video.
        query = [
            (name, value)
            for name, value in query
            if name.lower() not in _NON_IDENTIFYING_PARAMS
        ]
    if short:
        host, path = "www.youtube.com", "/watch"
        query = [("v", short.group(1))] + [q for q in query if q[0] != "v"]
    elif host in _YOUTUBE_HOSTS:
        host = "www.youtube.com"
    return urllib.parse.urlunsplit(
        (parts.scheme.lower(), host, path, urllib.parse.urlencode(query), "")
    )


def source_id_for(url: str) -> str:
    """A stable directory name for a URL, so a re-run reuses the download."""
    digest = hashlib.sha1(canonical_url(url).encode("utf-8")).hexdigest()[:12]
    return f"url-{digest}"


def _tool(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise IngestError(
            f"`{name}` is not on PATH. Install it and try again "
            f"(`pip install yt-dlp` for yt-dlp; ffmpeg ships with the project setup)."
        )
    return found


def _run(args: list[str], timeout: int = 1800) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, capture_output=True, text=True, timeout=timeout, check=False
    )


def probe_duration(path: str) -> float:
    """Duration in seconds via ffprobe, or 0.0 when it cannot be read."""
    try:
        result = _run(
            [
                _tool("ffprobe"),
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            timeout=60,
        )
        return float((result.stdout or "0").strip())
    except (IngestError, ValueError, subprocess.SubprocessError):
        return 0.0


#: yt-dlp downloads video and audio as `source.f137.mp4` / `source.f140.m4a`
#: and merges them into `source.mp4`. If the merge fails the fragments
#: stay behind, and the largest one is the video-only stream: picking it
#: would produce silent clips with no error anywhere.
_FRAGMENT_RE = re.compile(r"\.f\d+\.[A-Za-z0-9]+$")


def _find_video(directory: str) -> str:
    merged = os.path.join(directory, "source.mp4")
    if os.path.isfile(merged):
        return merged
    candidates = [
        os.path.join(directory, name)
        for name in sorted(os.listdir(directory))
        if name.lower().endswith(VIDEO_EXTENSIONS)
        and not _FRAGMENT_RE.search(name)
        and not name.lower().endswith((".part", ".ytdl"))
    ]
    if not candidates:
        raise IngestError(
            f"no merged video in {directory} (yt-dlp may have failed to merge "
            "video and audio; is ffmpeg on PATH?)"
        )
    return max(candidates, key=lambda p: os.path.getsize(p))


def load_manifest(directory: str) -> Source | None:
    """A previously downloaded source, if its file is still there."""
    path = os.path.join(directory, MANIFEST_NAME)
    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not os.path.isfile(str(data.get("path") or "")):
        return None
    known = {f for f in Source.__dataclass_fields__ if f != "raw"}
    return Source(**{k: v for k, v in data.items() if k in known})


def save_manifest(source: Source) -> str:
    path = os.path.join(source.directory, MANIFEST_NAME)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(source.to_dict(), file, ensure_ascii=False, indent=2)
    return path


def from_local(path: str, rights_note: str = "local file") -> Source:
    """Wrap a file already on disk as a source."""
    full = os.path.abspath(path)
    if not os.path.isfile(full):
        raise IngestError(f"no such file: {full}")
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", os.path.splitext(os.path.basename(full))[0])
    # The stem alone collides (two `video.mp4` in different folders), and
    # the transcript cache is keyed on this id, so the path and the file's
    # size and mtime go into it too: an edited file gets a fresh transcript.
    stat = os.stat(full)
    digest = hashlib.sha1(
        f"{full}|{stat.st_size}|{int(stat.st_mtime)}".encode("utf-8")
    ).hexdigest()[:8]
    return Source(
        id=f"local-{stem[:40]}-{digest}",
        path=full,
        title=os.path.splitext(os.path.basename(full))[0],
        duration_seconds=probe_duration(full),
        origin="local",
        fetched_at=_now(),
        rights_note=rights_note,
    )


def metadata(url: str) -> dict[str, Any]:
    """What yt-dlp knows about a URL, without downloading it."""
    result = _run(
        [
            _tool("yt-dlp"),
            "--dump-single-json",
            "--no-playlist",
            "--no-warnings",
            url,
        ],
        timeout=120,
    )
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()
        raise IngestError(
            "yt-dlp could not read that URL: "
            + (tail[-1] if tail else f"exit {result.returncode}")
        )
    try:
        data = json.loads(result.stdout)
    except ValueError as exc:
        raise IngestError(f"yt-dlp returned unreadable metadata: {exc}") from exc
    return data if isinstance(data, dict) else {}


def _base_language(code: str) -> str:
    """"es-419" and "es-orig" are both Spanish; "live_chat" is not a language."""
    return (code or "").strip().lower().split("-")[0]


def choose_caption_track(info: dict[str, Any], language: str = "") -> dict[str, str] | None:
    """Which caption track to fetch, or None when none is worth having.

    Pure, so the policy is tested rather than discovered:

      - a track a person wrote beats anything generated;
      - generated ("automatic") captions are only taken in the video's
        *original* language. YouTube offers automatic captions in a
        hundred languages, and all but one are machine translations of
        the recognised speech — burning a translation onto a clip as if
        it were what was said would be wrong;
      - with no language asked for, the video's own language is used,
        and if the platform does not say what that is, generated tracks
        are refused rather than guessed at.
    """
    original = _base_language(str(info.get("language") or ""))
    wanted = _base_language(language) or original
    if not wanted:
        return None

    def pick(tracks: dict[str, Any], preferred: tuple[str, ...]) -> str:
        keys = [k for k in (tracks or {}) if k and k != "live_chat"]
        for key in preferred:
            if key in keys:
                return key
        matches = sorted(k for k in keys if _base_language(k) == wanted)
        return matches[0] if matches else ""

    manual = pick(info.get("subtitles") or {}, (wanted,))
    if manual:
        return {"lang": manual, "kind": "manual"}

    if original and wanted == original:
        auto = pick(info.get("automatic_captions") or {}, (f"{wanted}-orig", wanted))
        if auto:
            return {"lang": auto, "kind": "auto"}
    return None


def fetch_captions(
    url: str, directory: str, info: dict[str, Any], language: str = ""
) -> tuple[str, str, str]:
    """Download the chosen caption track as WebVTT next to the video.

    Returns `(path, kind, language)`, all empty when there is no usable
    track or the fetch fails. Never raises: captions improve subtitles,
    and a video that downloaded fine must not be lost over them.
    """
    if clips_config.setting("captions") == "off":
        return "", "", ""
    track = choose_caption_track(info, language)
    if not track:
        return "", "", ""
    if track["kind"] == "auto" and clips_config.setting("captions") == "manual":
        return "", "", ""

    for stale in os.listdir(directory):
        if stale.startswith("captions.") and stale.endswith(".vtt"):
            try:
                os.remove(os.path.join(directory, stale))
            except OSError:
                pass

    flag = "--write-subs" if track["kind"] == "manual" else "--write-auto-subs"
    try:
        result = _run(
            [
                _tool("yt-dlp"),
                "--no-playlist",
                "--no-warnings",
                "--no-progress",
                "--skip-download",
                flag,
                "--sub-langs", track["lang"],
                "--sub-format", "vtt/best",
                "--convert-subs", "vtt",
                "-o", os.path.join(directory, "captions.%(ext)s"),
                url,
            ],
            timeout=180,
        )
    except (IngestError, subprocess.SubprocessError):
        return "", "", ""
    if result.returncode != 0:
        return "", "", ""
    found = sorted(
        name
        for name in os.listdir(directory)
        if name.startswith("captions.") and name.endswith(".vtt")
    )
    if not found:
        return "", "", ""
    return os.path.join(directory, found[0]), track["kind"], track["lang"]


def _attach_captions(source: Source, info: dict[str, Any], language: str) -> Source:
    path, kind, lang = fetch_captions(source.url, source.directory, info, language)
    source.captions_path, source.captions_kind, source.captions_language = path, kind, lang
    source.captions_checked = True
    return source


def _fragment_cache(url: str, fmt: dict[str, Any]) -> str:
    """Where a stream's fragments of one format are kept between runs.

    A sibling of the snapshots, not inside one: snapshots are per minute,
    fragments are forever (an aired fragment never changes). Not listed as
    a source, because it has no manifest.
    """
    return os.path.join(
        clips_config.sources_dir(),
        f"{source_id_for(url)}-fragments",
        str(fmt.get("format_id") or "default"),
    )


def _live_snapshot(
    url: str,
    info: dict[str, Any],
    rights_note: str,
    max_minutes: float,
    on_status=None,
) -> Source:
    """Capture the audio of everything a live stream has aired so far.

    Each run is its own snapshot (the stream keeps growing), named by the
    minute it was taken, so re-running a minute later reuses the work but a
    run an hour later sees the new hour.
    """
    from . import live

    say = on_status or (lambda message: None)
    stamp = _dt.datetime.now().strftime("%Y%m%d%H%M")
    source_id = f"{source_id_for(url)}-live-{stamp}"
    directory = clips_config.source_dir(source_id)
    existing = load_manifest(directory)
    if existing:
        return existing

    try:
        formats = live.live_formats(url)
        head = live.head_sequence(formats["audio"])
    except live.LiveError as exc:
        raise IngestError(str(exc)) from exc
    target = formats["target"]
    first, last = live.sequence_range(head, target, max_minutes)
    aired = (head + 1) * target / 60
    kept = (last - first + 1) * target / 60
    say(
        f"live stream, {aired:.0f} min aired so far"
        + (f"; taking the last {kept:.0f} min" if first > 0 else "")
        + f" — fetching audio ({last - first + 1} fragments)"
    )

    raw = os.path.join(directory, "audio.raw.m4a")
    try:
        live.fetch_range(
            formats["audio"], first, last, raw,
            on_progress=lambda done, total: say(f"audio {done}/{total} fragments"),
            cache_dir=_fragment_cache(url, formats["audio"]),
            refresh=lambda: live.live_formats(url)["audio"],
        )
        start_time = live.probe_start(raw)
        audio = live.remux([raw], os.path.join(directory, "audio.m4a"), ["0:a"])
    except live.LiveError as exc:
        # Fragments stay in the per-stream cache, so the next run resumes;
        # the half-built snapshot itself is useless and goes.
        shutil.rmtree(directory, ignore_errors=True)
        raise IngestError(str(exc)) from exc
    finally:
        if os.path.isfile(raw):
            try:
                os.remove(raw)
            except OSError:
                pass

    source = Source(
        id=source_id,
        path=audio,
        title=str(info.get("title") or formats.get("title") or ""),
        url=url,
        uploader=str(info.get("uploader") or info.get("channel") or ""),
        duration_seconds=probe_duration(audio),
        language=str(info.get("language") or ""),
        origin="live",
        fetched_at=_now(),
        rights_note=rights_note,
        # A stream offers live chat, not captions, while it is on air.
        captions_checked=True,
        live=True,
        live_first_sq=first,
        live_last_sq=last,
        live_target=target,
        live_start_time=start_time,
    )
    save_manifest(source)
    return source


_LIVE_FORMAT_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}

#: Re-resolve fragment URLs after this long; they expire after a few hours.
_LIVE_FORMAT_TTL = 1800.0


def _cached_live_formats(url: str) -> dict[str, Any]:
    import time as _time

    from . import live

    cached = _LIVE_FORMAT_CACHE.get(url)
    if cached and _time.time() - cached[0] < _LIVE_FORMAT_TTL:
        return cached[1]
    formats = live.live_formats(url)
    _LIVE_FORMAT_CACHE[url] = (_time.time(), formats)
    return formats


def materialize_span(source: Source, start: float, end: float, on_status=None) -> tuple[str, float]:
    """A file containing [start, end] of the source, and where 0 of it falls.

    Returns `(path, offset)`: the span begins at `start - offset` inside
    `path`. For an ordinary source that is the whole file and offset 0. For
    a live snapshot, only the video fragments covering the span are fetched
    and muxed into a small piece, cached beside the snapshot. If the stream
    has ended in the meantime, the span is cut from the finished video
    instead.
    """
    if not source.live:
        return source.path, 0.0

    from . import live

    say = on_status or (lambda message: None)
    piece = os.path.join(source.directory, f"piece-{int(start * 10)}-{int(end * 10)}.mp4")
    sidecar = piece + ".json"
    if os.path.isfile(piece) and os.path.isfile(sidecar):
        try:
            with open(sidecar, "r", encoding="utf-8") as file:
                return piece, float(json.load(file)["offset"])
        except (OSError, ValueError, KeyError):
            pass

    try:
        formats = _cached_live_formats(source.url)
        on_air = formats.get("is_live", False)
    except live.LiveError:
        on_air = False

    if on_air:
        first, last = live.span_sequences(
            start, end, source.live_target or formats["target"],
            source.live_first_sq, source.live_last_sq,
        )
        say(f"fetching video fragments {first}-{last}")
        v_raw, a_raw = piece + ".v.raw", piece + ".a.raw"
        try:
            live.fetch_range(
                formats["video"], first, last, v_raw,
                cache_dir=_fragment_cache(source.url, formats["video"]),
                refresh=lambda: live.live_formats(source.url)["video"],
            )
            live.fetch_range(
                formats["audio"], first, last, a_raw,
                cache_dir=_fragment_cache(source.url, formats["audio"]),
                refresh=lambda: live.live_formats(source.url)["audio"],
            )
            offset = live.probe_start(v_raw) - source.live_start_time
            live.remux([v_raw, a_raw], piece, ["0:v", "1:a"])
        except live.LiveError as exc:
            raise IngestError(str(exc)) from exc
        finally:
            for leftover in (v_raw, a_raw):
                if os.path.isfile(leftover):
                    try:
                        os.remove(leftover)
                    except OSError:
                        pass
    else:
        # The stream ended: its recording is now an ordinary video whose
        # clock matches the stream's, so cut the span out of it directly.
        lo = max(0.0, source.live_start_time + start - 1.0)
        hi = source.live_start_time + end + 1.0
        say("stream has ended; cutting the span from the recording")
        result = _run(
            [
                _tool("yt-dlp"), "--no-playlist", "--no-warnings", "--no-progress",
                "-f", FORMAT_SPEC, "--merge-output-format", "mp4",
                "--download-sections", f"*{lo:.2f}-{hi:.2f}",
                "--force-keyframes-at-cuts",
                "-o", piece, source.url,
            ]
        )
        if result.returncode != 0 or not os.path.isfile(piece):
            tail = (result.stderr or result.stdout or "").strip().splitlines()
            raise IngestError("could not cut the span: " + (tail[-1] if tail else "yt-dlp failed"))
        offset = lo - source.live_start_time

    with open(sidecar, "w", encoding="utf-8") as file:
        json.dump({"offset": offset, "start": start, "end": end}, file)
    return piece, offset


def download(
    url: str,
    rights_note: str,
    refresh: bool = False,
    max_minutes: float | None = None,
    language: str = "",
    on_status=None,
) -> Source:
    """Fetch a URL into its own directory and write its manifest.

    `rights_note` is recorded verbatim: the caller states on what basis
    this video may be clipped, and that statement travels with every clip
    cut from it.
    """
    url = (url or "").strip()
    if not url:
        raise IngestError("no URL given")

    directory = clips_config.source_dir(source_id_for(url))
    if not refresh:
        existing = load_manifest(directory)
        if existing:
            if not existing.captions_checked:
                # Downloaded before captions were fetched: look once.
                try:
                    _attach_captions(existing, metadata(url), language)
                except IngestError:
                    existing.captions_checked = True
                save_manifest(existing)
            return existing

    info = metadata(url)
    duration = float(info.get("duration") or 0.0)
    ceiling = float(
        max_minutes
        if max_minutes is not None
        else clips_config.setting("max_source_minutes")
    )
    if info.get("is_live"):
        # What has aired so far, capped to the newest `ceiling` minutes.
        return _live_snapshot(url, info, rights_note, ceiling, on_status=on_status)
    if duration and ceiling and duration > ceiling * 60:
        raise IngestError(
            f"source is {duration / 60:.0f} min, over the {ceiling:.0f} min limit "
            f"(raise clips.max_source_minutes, or pass --max-minutes)"
        )

    result = _run(
        [
            _tool("yt-dlp"),
            "--no-playlist",
            "--no-warnings",
            "--no-progress",
            "-f", FORMAT_SPEC,
            "--merge-output-format", "mp4",
            "-o", os.path.join(directory, "source.%(ext)s"),
            url,
        ]
    )
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()
        raise IngestError(
            "yt-dlp failed: " + (tail[-1] if tail else f"exit {result.returncode}")
        )

    path = _find_video(directory)
    source = Source(
        id=source_id_for(url),
        path=path,
        title=str(info.get("title") or ""),
        url=url,
        uploader=str(info.get("uploader") or info.get("channel") or ""),
        duration_seconds=duration or probe_duration(path),
        language=str(info.get("language") or ""),
        origin="download",
        fetched_at=_now(),
        rights_note=rights_note,
    )
    _attach_captions(source, info, language)
    save_manifest(source)
    return source


def resolve(target: str, rights_note: str, refresh: bool = False, **kwargs) -> Source:
    """A URL or a local path, whichever the caller gave."""
    if re.match(r"^https?://", (target or "").strip(), re.IGNORECASE):
        return download(target, rights_note, refresh=refresh, **kwargs)
    return from_local(target, rights_note)
