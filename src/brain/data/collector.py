"""Reading channel history, and recording metric snapshots over time.

`.mp/youtube.json` holds exactly one set of numbers per video: whatever the
last sync saw. That is enough to ask "did this video do well?" and not
enough to ask "how fast did it get there?" — and growth velocity is one of
the strongest early signals there is (spec §6).

So this module does two things:

  - **Read** the cache into `ChannelContext` objects (pure, no writes).
  - **Append** a timestamped snapshot of every video's counters to
    `.mp/brain/metrics_history.jsonl`. Nothing derives velocity out of thin
    air; the only way to have that data later is to start keeping it now,
    which is why Phase 0 of the plan is "log everything, decide nothing".

Snapshots are append-only, one JSON object per line, deduplicated against
the previous reading so a sync that changed nothing costs no disk.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from typing import Any, Iterable

from ..config import cache_file, youtube_cache_path
from ..constants import LABEL_WINDOWS_HOURS, METRICS_HISTORY_FILE
from ..schema import ChannelContext, VideoRecord, now_utc, parse_date


def load_cache(path: str | None = None) -> dict[str, Any]:
    """Whole youtube.json, or an empty shell when it does not exist yet."""
    target = path or youtube_cache_path()
    try:
        with open(target, "r", encoding="utf-8") as file:
            parsed = json.load(file)
    except (OSError, ValueError):
        return {"accounts": []}
    if not isinstance(parsed, dict):
        return {"accounts": []}
    accounts = parsed.get("accounts")
    return {"accounts": accounts if isinstance(accounts, list) else []}


def load_channels(
    path: str | None = None,
    now: _dt.datetime | None = None,
    include_reference: bool = False,
) -> list[ChannelContext]:
    """Every account in the cache as a `ChannelContext`.

    Reference channels are excluded unless asked for: they are training
    material, not channels this installation publishes to, and every caller
    that lists "your channels" must not see them.
    """
    moment = now or now_utc()
    channels = [
        ChannelContext.from_account(account, moment)
        for account in load_cache(path).get("accounts", [])
        if isinstance(account, dict)
    ]
    if include_reference:
        from .reference import reference_channels

        channels.extend(reference_channels(moment))
    return channels


def find_channel(
    account_id: str, path: str | None = None, now: _dt.datetime | None = None
) -> ChannelContext | None:
    for channel in load_channels(path, now):
        if channel.account_id == str(account_id):
            return channel
    return None


def all_videos(path: str | None = None) -> list[VideoRecord]:
    """Published videos across your own channels - the snapshot population.

    Reference channels are deliberately absent: snapshots exist to track how
    *your* videos accumulate views over time, and re-recording someone
    else's totals every day would bloat the log for nothing.
    """
    videos: list[VideoRecord] = []
    for channel in load_channels(path):
        videos.extend(channel.published())
    return videos


# -- Metric snapshots ---------------------------------------------------


def snapshot_path() -> str:
    return cache_file(METRICS_HISTORY_FILE)


def _snapshot_row(
    video: VideoRecord, observed_at: _dt.datetime
) -> dict[str, Any] | None:
    if video.views is None:
        return None
    age_hours = None
    if video.published_at:
        age_hours = (observed_at - video.published_at).total_seconds() / 3600.0
        if age_hours < 0:
            age_hours = 0.0
    return {
        "observed_at": observed_at.isoformat(),
        "account_id": video.account_id,
        "video_id": video.video_id,
        "published_at": video.published_at.isoformat() if video.published_at else None,
        "age_hours": None if age_hours is None else round(age_hours, 3),
        "views": video.views,
        "likes": video.likes,
        "comments": video.comments,
        "avg_percentage_viewed": video.avg_percentage_viewed,
    }


def _counters(row: dict[str, Any]) -> tuple:
    return (
        row.get("views"),
        row.get("likes"),
        row.get("comments"),
        row.get("avg_percentage_viewed"),
    )


def record_snapshot(
    path: str | None = None,
    now: _dt.datetime | None = None,
    videos: list[VideoRecord] | None = None,
) -> dict[str, int]:
    """Append the current counters for every video. Returns a small summary.

    A snapshot is skipped when a video's counters are identical to its last
    recorded reading — the sync scripts stamp `stats_synced_at` on every run
    whether or not anything moved, and duplicated rows would distort any
    later velocity calculation.
    """
    observed_at = now or now_utc()
    population = videos if videos is not None else all_videos(path)

    latest = _latest_by_video()
    rows: list[dict[str, Any]] = []
    for video in population:
        row = _snapshot_row(video, observed_at)
        if row is None:
            continue
        previous = latest.get(video.video_id)
        if previous is not None and _counters(previous) == _counters(row):
            continue
        rows.append(row)

    if rows:
        target = snapshot_path()
        with open(target, "a", encoding="utf-8") as file:
            for row in rows:
                file.write(json.dumps(row, ensure_ascii=False) + "\n")
    return {
        "videos": len(population),
        "written": len(rows),
        "skipped_unchanged": len(population) - len(rows),
    }


def load_snapshots(account_id: str | None = None) -> list[dict[str, Any]]:
    """Every recorded snapshot, oldest first. Corrupt lines are skipped."""
    target = snapshot_path()
    if not os.path.exists(target):
        return []
    rows: list[dict[str, Any]] = []
    with open(target, "r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            if account_id and row.get("account_id") != account_id:
                continue
            rows.append(row)
    rows.sort(key=lambda r: str(r.get("observed_at") or ""))
    return rows


def _latest_by_video() -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in load_snapshots():
        video_id = str(row.get("video_id") or "")
        if video_id:
            latest[video_id] = row
    return latest


def snapshots_by_video(account_id: str | None = None) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in load_snapshots(account_id):
        grouped.setdefault(str(row.get("video_id") or ""), []).append(row)
    return grouped


def windowed_views(
    snapshots: Iterable[dict[str, Any]],
    windows: tuple[int, ...] = LABEL_WINDOWS_HOURS,
) -> dict[str, float]:
    """Views at each label window, from a single video's snapshots (spec §112).

    A window is filled by the last snapshot taken at or before it, and only
    when a snapshot actually exists in that period — never interpolated.
    A guessed `views_1h` would be indistinguishable from a measured one
    downstream, which is exactly the kind of quiet fiction that makes a
    model look accurate and behave badly.
    """
    ordered = sorted(
        (row for row in snapshots if row.get("age_hours") is not None),
        key=lambda r: float(r["age_hours"]),
    )
    result: dict[str, float] = {}
    for window in windows:
        eligible = [row for row in ordered if float(row["age_hours"]) <= window]
        if not eligible:
            continue
        views = eligible[-1].get("views")
        if views is not None:
            result[f"views_{window}h"] = float(views)
    return result


def view_velocity(windowed: dict[str, float]) -> dict[str, float]:
    """Views per hour within each measured window (spec §6)."""
    velocity: dict[str, float] = {}
    for key, value in windowed.items():
        try:
            hours = float(key.split("_")[1].rstrip("h"))
        except (IndexError, ValueError):
            continue
        if hours > 0:
            velocity[f"velocity_{int(hours)}h"] = value / hours
    return velocity


def latest_snapshot_age_hours(video_id: str) -> float | None:
    rows = snapshots_by_video().get(video_id) or []
    if not rows:
        return None
    ages = [r.get("age_hours") for r in rows if r.get("age_hours") is not None]
    return max(ages) if ages else None


def coverage_summary() -> dict[str, Any]:
    """How much history the snapshot log has accumulated so far."""
    rows = load_snapshots()
    if not rows:
        return {"snapshots": 0, "videos": 0, "first": None, "last": None}
    videos = {str(row.get("video_id") or "") for row in rows}
    return {
        "snapshots": len(rows),
        "videos": len(videos),
        "first": rows[0].get("observed_at"),
        "last": rows[-1].get("observed_at"),
        "with_early_window": sum(
            1 for row in rows
            if row.get("age_hours") is not None and float(row["age_hours"]) <= 24
        ),
    }


def parse_observed_at(row: dict[str, Any]) -> _dt.datetime | None:
    return parse_date(row.get("observed_at"))
