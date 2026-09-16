"""Reference channels: other people's history, used only to train on.

Savirox has 83 videos. Nothing honest can be learned from 83 rows, so the
fastest way to give Brain something to learn from is other channels in the
same niche — the cold-start transfer the plan describes as global -> niche
-> channel (spec §84).

Two decisions this module encodes:

  - **They live outside `.mp/youtube.json`.** That file is the list of
    channels the pipeline can *generate for and upload to*. A competitor's
    channel has no business in it: it would show up in the webapp as a
    publishable target. Reference channels live in
    `.mp/brain/reference_channels.json` and are visible only to Brain.

  - **Full history, not just the hits.** "Train it on the best videos" is
    the one thing that cannot work: `normalized_views` is a video's views
    over its channel's recent median, so a dataset of only winners has a
    median made of winners and every label collapses to ~1.0. Winners are
    only recognisable next to the losers they beat. The whole history goes
    in; the label is what marks the good ones.

Metadata is fetched one video at a time rather than from the flat channel
listing. The listing is far cheaper but has no upload date — without which
peer windows, maturity and the chronological split are all meaningless —
and it serves the *default-language* title, which for a dubbed channel like
"¿Y si?" is English while the real title is Spanish.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import time
from typing import Any, Callable

from ..config import cache_file
from ..schema import ChannelContext, now_utc

REFERENCE_FILE = "reference_channels.json"

#: Videos imported per channel by default. Recent history is what predicts
#: today; a 2019 upload says little about the current algorithm, and every
#: video costs a metadata round-trip.
DEFAULT_LIMIT = 300
#: Pause between per-video fetches, matching `scripts/sync_youtube_cache.py`.
DEFAULT_SLEEP = 0.2


def reference_path() -> str:
    return cache_file(REFERENCE_FILE)


def load_reference() -> dict[str, Any]:
    """The reference store, or an empty shell."""
    try:
        with open(reference_path(), "r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, ValueError):
        return {"accounts": []}
    if not isinstance(data, dict):
        return {"accounts": []}
    accounts = data.get("accounts")
    return {"accounts": accounts if isinstance(accounts, list) else []}


def save_reference(data: dict[str, Any]) -> str:
    path = reference_path()
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)
    return path


def reference_accounts() -> list[dict[str, Any]]:
    return [a for a in load_reference().get("accounts", []) if isinstance(a, dict)]


def reference_channels(now: _dt.datetime | None = None) -> list[ChannelContext]:
    """Reference accounts as `ChannelContext`, flagged as reference."""
    moment = now or now_utc()
    return [ChannelContext.from_account(a, moment) for a in reference_accounts()]


def normalize_handle(handle: str) -> str:
    """Accept '@name', 'youtube.com/@name' or a full URL. Return '@name'."""
    import re

    text = str(handle or "").strip().rstrip("/")
    match = re.search(r"@([A-Za-z0-9._-]+)", text)
    if match:
        return "@" + match.group(1)
    return text


# -- Fetching -----------------------------------------------------------


def _flat_tab(url: str) -> list[dict[str, Any]]:
    """Video ids from one channel tab, newest first. [] on any failure."""
    import yt_dlp

    options = {
        "extract_flat": True, "skip_download": True, "quiet": True,
        "no_warnings": True, "ignoreerrors": True,
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception:
        return []
    entries = [e for e in ((info or {}).get("entries") or []) if e]
    return [
        {"id": e.get("id"), "title": e.get("title") or "",
         "view_count": e.get("view_count"), "duration": e.get("duration")}
        for e in entries if e.get("id")
    ]


def _channel_header(handle: str) -> dict[str, Any]:
    """Channel name and subscriber count."""
    import yt_dlp

    options = {
        "extract_flat": True, "skip_download": True, "quiet": True,
        "no_warnings": True, "ignoreerrors": True, "playlist_items": "1",
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(
                f"https://www.youtube.com/{handle}/videos", download=False
            )
    except Exception:
        return {}
    info = info or {}
    return {
        "nickname": info.get("channel") or info.get("uploader") or handle,
        "channel_id": info.get("channel_id") or "",
        "subscriber_count": info.get("channel_follower_count"),
    }


def fetch_video(video_id: str) -> dict[str, Any]:
    """Full metadata for one video. Returns {} when it cannot be read."""
    import yt_dlp

    options = {"quiet": True, "no_warnings": True, "skip_download": True,
               "ignoreerrors": True}
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(
                f"https://www.youtube.com/watch?v={video_id}", download=False
            )
    except Exception:
        return {}
    if not info:
        return {}

    timestamp = info.get("timestamp")
    if timestamp:
        date = _dt.datetime.fromtimestamp(int(timestamp)).strftime("%Y-%m-%d %H:%M:%S")
    else:
        upload = str(info.get("upload_date") or "")
        date = f"{upload[0:4]}-{upload[4:6]}-{upload[6:8]} 00:00:00" if len(upload) == 8 else ""

    duration = info.get("duration")
    return {
        "title": info.get("title") or "",
        "description": (info.get("description") or "").strip(),
        "date": date,
        "duration_seconds": float(duration) if isinstance(duration, (int, float)) else None,
        "view_count": info.get("view_count"),
        "like_count": info.get("like_count"),
        "comment_count": info.get("comment_count"),
        "language": info.get("language") or "",
    }


def _split_limit(long_count: int, short_count: int, limit: int) -> tuple[int, int]:
    """Split a per-channel budget across tabs, keeping the format mix.

    Taking `limit` from one tab would misrepresent a channel that publishes
    mostly Shorts as a long-form channel, and format is one of the strongest
    features in the model.
    """
    total = long_count + short_count
    if total <= limit:
        return long_count, short_count
    longs = round(limit * long_count / total)
    return min(long_count, longs), min(short_count, limit - longs)


def import_channel(
    handle: str,
    limit: int = DEFAULT_LIMIT,
    sleep: float = DEFAULT_SLEEP,
    progress: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Import one reference channel into the Brain-only store.

    Idempotent by handle: re-running replaces that channel's entry. Returns
    a summary; never raises on a single unreadable video.
    """
    handle = normalize_handle(handle)
    base = f"https://www.youtube.com/{handle}"

    header = _channel_header(handle)
    longs = _flat_tab(f"{base}/videos")
    shorts = _flat_tab(f"{base}/shorts")
    if not longs and not shorts:
        return {"handle": handle, "imported": 0,
                "error": "yt-dlp returned no videos (wrong handle or rate-limited)"}

    take_long, take_short = _split_limit(len(longs), len(shorts), limit)
    selected = (
        [(v, False) for v in longs[:take_long]] + [(v, True) for v in shorts[:take_short]]
    )
    progress(
        f"   {handle}: {len(longs)} largos + {len(shorts)} shorts disponibles; "
        f"bajando {take_long} + {take_short} = {len(selected)}"
    )

    videos: list[dict[str, Any]] = []
    failed = 0
    synced_at = now_utc().strftime("%Y-%m-%d %H:%M:%S")
    for index, (entry, is_short) in enumerate(selected, start=1):
        meta = fetch_video(entry["id"])
        if not meta or not meta.get("date") or meta.get("view_count") is None:
            failed += 1
        else:
            videos.append({
                "title": meta["title"],
                "description": meta["description"],
                "subject": meta["title"],
                "url": f"https://www.youtube.com/watch?v={entry['id']}",
                "date": meta["date"],
                "is_short": bool(is_short),
                "duration_seconds": meta["duration_seconds"],
                "view_count": meta["view_count"],
                "like_count": meta["like_count"],
                "comment_count": meta["comment_count"],
                "stats_synced_at": synced_at,
            })
        if index % 25 == 0 or index == len(selected):
            progress(f"      {index}/{len(selected)} ({failed} fallidos)")
        time.sleep(sleep)

    account = {
        "id": f"ref:{handle.lstrip('@').lower()}",
        "nickname": header.get("nickname") or handle,
        "youtube_handle": handle,
        "channel_id": header.get("channel_id", ""),
        "subscriber_count": header.get("subscriber_count"),
        "reference": True,
        "imported_at": now_utc().isoformat(),
        "videos": videos,
    }

    data = load_reference()
    data["accounts"] = [
        a for a in data.get("accounts", [])
        if normalize_handle(a.get("youtube_handle", "")) != handle
    ]
    data["accounts"].append(account)
    save_reference(data)

    return {
        "handle": handle,
        "nickname": account["nickname"],
        "subscribers": account["subscriber_count"],
        "available": len(longs) + len(shorts),
        "imported": len(videos),
        "failed": failed,
    }


def remove_channel(handle: str) -> bool:
    handle = normalize_handle(handle)
    data = load_reference()
    before = len(data.get("accounts", []))
    data["accounts"] = [
        a for a in data.get("accounts", [])
        if normalize_handle(a.get("youtube_handle", "")) != handle
    ]
    if len(data["accounts"]) == before:
        return False
    save_reference(data)
    return True


def summary() -> list[dict[str, Any]]:
    """One row per reference channel, for `brain reference` and status."""
    rows = []
    for account in reference_accounts():
        videos = account.get("videos") or []
        views = [
            v.get("view_count") for v in videos
            if isinstance(v.get("view_count"), (int, float))
        ]
        dates = sorted(str(v.get("date") or "") for v in videos if v.get("date"))
        rows.append({
            "handle": account.get("youtube_handle", ""),
            "nickname": account.get("nickname", ""),
            "subscribers": account.get("subscriber_count"),
            "videos": len(videos),
            "median_views": sorted(views)[len(views) // 2] if views else None,
            "first": dates[0][:10] if dates else None,
            "last": dates[-1][:10] if dates else None,
        })
    return rows
