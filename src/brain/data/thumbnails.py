"""Downloading the cover image of already-published videos.

The thumbnail feature group was 0% covered: `thumbnail_path` is only set for
videos this pipeline rendered itself, so every imported video had five blank
columns. For a channel whose views come from cold browse and recommendation,
the cover is plausibly the single strongest thing about a video — and it is
sitting on YouTube's CDN, one request away, for every video ever published.

For a Short the cover *is* the frame the feed shows, so this is not a
side-channel: it is the thing the viewer decides on.

No API key and no yt-dlp: `i.ytimg.com/vi/<id>/<size>.jpg` is public and
static. Sizes are tried largest-first because a bigger source makes the
contrast and edge statistics more stable; `hqdefault` always exists, so the
fallback chain terminates.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Callable, Iterable

from ..config import cache_dir, youtube_cache_path
from ..schema import extract_video_id

#: Largest first. `hqdefault` is always present, even for old Shorts.
SIZES = ("maxresdefault", "sddefault", "hqdefault")
TIMEOUT = 12
#: A 404 from the CDN is served as a tiny grey placeholder, not an error.
#: Anything under this many bytes is that placeholder, not a thumbnail.
MIN_BYTES = 2048


def thumbnails_dir() -> str:
    path = os.path.join(cache_dir(), "thumbnails")
    os.makedirs(path, exist_ok=True)
    return path


def thumbnail_path(video_id: str) -> str:
    return os.path.join(thumbnails_dir(), f"{video_id}.jpg")


def download_thumbnail(video_id: str, overwrite: bool = False) -> str:
    """Fetch one video's cover. Returns the local path, or "" on failure."""
    if not video_id:
        return ""
    target = thumbnail_path(video_id)
    if os.path.exists(target) and not overwrite:
        return target

    for size in SIZES:
        url = f"https://i.ytimg.com/vi/{video_id}/{size}.jpg"
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "MoneyPrinterLargo/1.0"}
            )
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                payload = response.read()
        except (urllib.error.URLError, OSError, ValueError):
            continue
        if len(payload) < MIN_BYTES:
            continue
        tmp_path = target + ".tmp"
        with open(tmp_path, "wb") as file:
            file.write(payload)
        os.replace(tmp_path, target)
        return target
    return ""


def _apply_to_accounts(
    accounts: Iterable[dict[str, Any]],
    overwrite: bool,
    progress: Callable[[str], None],
    label: str,
) -> dict[str, int]:
    fetched = failed = skipped = 0
    for account in accounts:
        for video in account.get("videos") or []:
            video_id = extract_video_id(str(video.get("url") or ""))
            if not video_id:
                continue
            if video.get("thumbnail_path") and os.path.exists(video["thumbnail_path"]) \
                    and not overwrite:
                skipped += 1
                continue
            path = download_thumbnail(video_id, overwrite)
            if path:
                video["thumbnail_path"] = path
                fetched += 1
            else:
                failed += 1
            if (fetched + failed) % 50 == 0:
                progress(f"      {label}: {fetched} bajadas, {failed} fallidas")
    return {"fetched": fetched, "failed": failed, "skipped": skipped}


def backfill(
    include_reference: bool = True,
    overwrite: bool = False,
    progress: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Download covers for every published video and record the paths.

    Writes `thumbnail_path` back into `.mp/youtube.json` and the reference
    store, so the feature layer picks them up on the next dataset build.
    """
    from . import reference

    summary: dict[str, Any] = {}

    path = youtube_cache_path()
    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, ValueError):
        data = None

    if isinstance(data, dict) and data.get("accounts"):
        progress("   tus canales...")
        summary["own"] = _apply_to_accounts(
            data["accounts"], overwrite, progress, "propios"
        )
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=4, ensure_ascii=False)
        os.replace(tmp_path, path)

    if include_reference:
        store = reference.load_reference()
        if store.get("accounts"):
            progress("   canales de referencia...")
            summary["reference"] = _apply_to_accounts(
                store["accounts"], overwrite, progress, "referencia"
            )
            reference.save_reference(store)

    total = {"fetched": 0, "failed": 0, "skipped": 0}
    for block in summary.values():
        for key in total:
            total[key] += block[key]
    summary["total"] = total
    return summary


def coverage() -> dict[str, int]:
    """How many published videos currently have a readable cover on disk."""
    from . import collector

    have = missing = 0
    for channel in collector.load_channels(include_reference=True):
        for video in channel.published():
            path = video.thumbnail_path
            if path and os.path.exists(path):
                have += 1
            else:
                missing += 1
    return {"with_thumbnail": have, "without": missing}
