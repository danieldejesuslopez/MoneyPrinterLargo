"""Turn raw counts into channel-relative, comparable labels (spec §5, §7-§9).

Raw `view_count` is close to useless as a training target: it is dominated
by how big the channel was and how long the video has been up. A 3,000-view
video on a channel whose median is 800 is a hit; on a channel whose median
is 20,000 it is a failure. So every label here is expressed *relative to the
videos that came before it on the same channel*.

Two rules keep this honest:

  - **Peers are strictly prior.** The baseline for a video published in
    March is built only from videos published before March. Using the whole
    channel history would leak the future into the label and make backtests
    look better than reality (spec §114).
  - **Peers are time-adjacent.** The most recent N prior videos, not all of
    them, so a channel that grew 10x last quarter is not compared against
    its own early days.

When a channel has too little history to normalize against, the label falls
back to a global baseline and says so via `normalization_basis`; downstream
code can then weight or exclude those rows instead of trusting them blindly.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Iterable

from ..constants import (
    LABEL_MATURE_DAYS,
    TARGET_COMMENT_RATE,
    TARGET_CTR,
    TARGET_LIKE_RATE,
    TARGET_NORMALIZED_VIEWS,
    TARGET_RETENTION,
    TARGET_SUBSCRIBER_RATE,
    TARGET_WATCH_MINUTES,
)
from ..schema import VideoRecord, now_utc, success_class

#: How many prior videos form the comparison window.
PEER_WINDOW = 20
#: Below this many usable peers a channel-relative label is not credible.
MIN_PEERS = 3
#: Assumed length of a Short when the pipeline recorded no duration. Only
#: used for the derived watch-time label, and flagged as derived.
DEFAULT_SHORT_SECONDS = 45.0


def median(values: Iterable[float]) -> float | None:
    ordered = sorted(v for v in values if v is not None)
    if not ordered:
        return None
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def peer_window(
    record: VideoRecord,
    history: list[VideoRecord],
    window: int | None = None,
) -> list[VideoRecord]:
    """The `window` videos published most recently *before* `record`.

    Videos without a publish date or without views are dropped: they cannot
    anchor a baseline.

    `window` defaults to the module's `PEER_WINDOW` read *at call time*, not
    bound as a default argument: a bound default silently ignores any later
    change to the constant, which is exactly how an experiment that varied
    the window once reported five identical results.
    """
    window = PEER_WINDOW if window is None else window
    if not record.published_at:
        return []
    prior = [
        video for video in history
        if video.video_id != record.video_id
        and video.published_at
        and video.published_at < record.published_at
        and video.views is not None
    ]
    prior.sort(key=lambda v: v.published_at, reverse=True)
    return prior[:window]


def channel_baseline(
    history: list[VideoRecord],
    moment: _dt.datetime | None = None,
    window: int | None = None,
) -> dict[str, Any]:
    """Summary of what "normal" looks like on this channel at `moment`.

    Shared by the label maths and the channel feature group, so a prediction
    and its eventual label are measured against the same yardstick - which
    is why the window is read at call time, like `peer_window`: an
    experiment that changes one must change both.
    """
    window = PEER_WINDOW if window is None else window
    moment = moment or now_utc()
    prior = [
        video for video in history
        if video.published_at and video.published_at < moment
    ]
    prior.sort(key=lambda v: v.published_at, reverse=True)
    recent = prior[:window]

    views = [v.views for v in recent if v.views is not None]
    retentions = [
        v.avg_percentage_viewed for v in recent if v.avg_percentage_viewed is not None
    ]
    likes_rates = [
        v.likes / v.views for v in recent
        if v.likes is not None and v.views
    ]

    last_upload = recent[0].published_at if recent else None
    first_upload = prior[-1].published_at if prior else None
    week_ago = moment - _dt.timedelta(days=7)
    month_ago = moment - _dt.timedelta(days=30)

    return {
        "count": len(prior),
        "window_count": len(recent),
        "median_views": median(views),
        "mean_views": (sum(views) / len(views)) if views else None,
        "max_views": max(views) if views else None,
        "median_retention": median(retentions),
        "median_like_rate": median(likes_rates),
        "short_ratio": (
            sum(1 for v in recent if v.is_short) / len(recent) if recent else None
        ),
        "days_since_last_upload": (
            (moment - last_upload).total_seconds() / 86400.0 if last_upload else None
        ),
        "channel_age_days": (
            (moment - first_upload).total_seconds() / 86400.0 if first_upload else None
        ),
        "uploads_last_7d": sum(1 for v in prior if v.published_at >= week_ago),
        "uploads_last_30d": sum(1 for v in prior if v.published_at >= month_ago),
    }


def momentum(history: list[VideoRecord], moment: _dt.datetime) -> float | None:
    """Median views of the last 5 videos over the 15 before them.

    Above 1.0 the channel is on the way up; below, it is cooling. Without
    this, a model credits the *topic* for an uplift that the channel's own
    growth produced (spec §82).
    """
    prior = [
        v for v in history
        if v.published_at and v.published_at < moment and v.views is not None
    ]
    prior.sort(key=lambda v: v.published_at, reverse=True)
    if len(prior) < 8:
        return None
    recent = median(v.views for v in prior[:5])
    older = median(v.views for v in prior[5:20])
    if not recent or not older:
        return None
    return recent / older


def normalized_views(
    record: VideoRecord,
    peers: list[VideoRecord],
    global_median: float | None = None,
) -> tuple[float | None, str]:
    """Views as a multiple of the channel's recent median (spec §5).

    Returns `(value, basis)` where basis is "channel", "global" or "none",
    so callers can tell a well-grounded label from a fallback.
    """
    if record.views is None:
        return None, "none"
    peer_views = [v.views for v in peers if v.views is not None]
    if len(peer_views) >= MIN_PEERS:
        baseline = median(peer_views)
        if baseline and baseline > 0:
            return record.views / baseline, "channel"
    if global_median and global_median > 0:
        return record.views / global_median, "global"
    return None, "none"


def derived_watch_minutes(record: VideoRecord) -> tuple[float | None, bool]:
    """Estimated minutes watched, and whether it had to be derived.

    YouTube Analytics reports `estimatedMinutesWatched` directly, but this
    project reads Studio through a scraped page and does not have it yet.
    Until then: views x duration x average-percentage-viewed. Marked derived
    so a future real feed can replace it without silently mixing two
    different quantities in one column.
    """
    if record.views is None or record.avg_percentage_viewed is None:
        return None, False
    duration = record.duration_seconds
    derived = duration is None
    if derived:
        duration = DEFAULT_SHORT_SECONDS if record.is_short else None
    if not duration:
        return None, False
    minutes = record.views * duration * (record.avg_percentage_viewed / 100.0) / 60.0
    return minutes, True


def engagement_rates(record: VideoRecord) -> dict[str, float | None]:
    if not record.views:
        return {TARGET_LIKE_RATE: None, TARGET_COMMENT_RATE: None,
                TARGET_SUBSCRIBER_RATE: None}
    return {
        TARGET_LIKE_RATE: (
            record.likes / record.views if record.likes is not None else None
        ),
        TARGET_COMMENT_RATE: (
            record.comments / record.views if record.comments is not None else None
        ),
        TARGET_SUBSCRIBER_RATE: (
            record.subscribers_gained / record.views
            if record.subscribers_gained is not None else None
        ),
    }


def build_labels(
    record: VideoRecord,
    peers: list[VideoRecord],
    global_median: float | None = None,
) -> dict[str, Any]:
    """Every target Brain can learn for one published video.

    Targets with no ground truth stay `None` — never zero. A missing CTR is
    not a CTR of zero, and training on the difference is how a model learns
    to predict the sync schedule instead of the audience.
    """
    views_multiple, basis = normalized_views(record, peers, global_median)
    watch_minutes, watch_derived = derived_watch_minutes(record)
    rates = engagement_rates(record)

    targets: dict[str, float | None] = {
        TARGET_NORMALIZED_VIEWS: views_multiple,
        TARGET_RETENTION: (
            record.avg_percentage_viewed / 100.0
            if record.avg_percentage_viewed is not None else None
        ),
        TARGET_WATCH_MINUTES: watch_minutes,
        TARGET_LIKE_RATE: rates[TARGET_LIKE_RATE],
        TARGET_COMMENT_RATE: rates[TARGET_COMMENT_RATE],
        TARGET_SUBSCRIBER_RATE: rates[TARGET_SUBSCRIBER_RATE],
        TARGET_CTR: (record.ctr / 100.0 if record.ctr is not None else None),
    }
    return {
        "targets": targets,
        "normalization_basis": basis,
        "watch_minutes_derived": watch_derived,
        "peer_count": len(peers),
        "success_class": success_class(views_multiple),
    }


def is_mature(
    record: VideoRecord,
    now: _dt.datetime | None = None,
    days: float = LABEL_MATURE_DAYS,
) -> bool:
    """Has this video had enough time for its numbers to settle (spec §111)?

    Maturity is measured at the last metrics *sync*, not now: a video posted
    a year ago whose stats were scraped an hour after publishing still only
    carries one-hour numbers.
    """
    if not record.published_at:
        return False
    observed = record.stats_synced_at or now or now_utc()
    return (observed - record.published_at).total_seconds() / 86400.0 >= days


def reward(targets: dict[str, float | None], weights: dict[str, float]) -> float | None:
    """Weighted scalar reward from whatever targets are present (spec §94).

    Weights are renormalized over the *available* targets, so a channel with
    no retention data still gets a comparable reward from the rest instead
    of being dragged toward zero by missing columns.
    """
    scaled: dict[str, float] = {}
    for name, value in (targets or {}).items():
        if value is None or name not in weights:
            continue
        scaled[name] = _to_unit_scale(name, value)
    if not scaled:
        return None
    total_weight = sum(weights[name] for name in scaled) or 1.0
    return sum(weights[name] * value for name, value in scaled.items()) / total_weight


def _to_unit_scale(name: str, value: float) -> float:
    """Map a target onto a roughly 0-1 scale so the weighted sum is sane.

    The scale points are conventions, not measurements: 1.0 means "channel
    median" for view multiples, and the engagement divisors are typical
    good-video rates. They only have to be consistent, because reward is
    used for ranking, never reported as a real-world quantity.
    """
    if name == TARGET_NORMALIZED_VIEWS:
        return min(1.0, value / 3.0)
    if name == TARGET_WATCH_MINUTES:
        return min(1.0, value / 1000.0)
    if name == TARGET_RETENTION:
        return min(1.0, value)
    if name == TARGET_CTR:
        return min(1.0, value / 0.10)
    if name == TARGET_LIKE_RATE:
        return min(1.0, value / 0.06)
    if name == TARGET_COMMENT_RATE:
        return min(1.0, value / 0.01)
    if name == TARGET_SUBSCRIBER_RATE:
        return min(1.0, value / 0.02)
    return min(1.0, max(0.0, value))
