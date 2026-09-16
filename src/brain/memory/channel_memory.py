"""The channel's digital twin: what works here, what does not, and when.

Statistical memory, not LLM memory (spec §47, §48). An LLM asked to
"remember what worked" will produce a plausible summary; this produces
counts. The two are complementary — the profile here is what gets injected
into prompts so the creative layer is briefed on facts rather than vibes.

Topic clusters are built with greedy agglomeration over token overlap rather
than KMeans or HDBSCAN (spec §51). At a few hundred titles the result is
equivalent, it needs no dependency, and — unlike a centroid in embedding
space — every cluster can be named by the words that define it, which is
what makes the output readable.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from typing import Any

from ..config import cache_dir
from ..data import normalizer as norm
from ..features import lexicon as lx
from ..schema import ChannelContext, VideoRecord, now_utc

#: Token overlap at which two videos join the same topic cluster.
CLUSTER_THRESHOLD = 0.28
#: Clusters below this size are noise, not a pattern.
MIN_CLUSTER_SIZE = 2


def profiles_dir() -> str:
    path = os.path.join(cache_dir(), "channels")
    os.makedirs(path, exist_ok=True)
    return path


def profile_path(account_id: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(account_id))
    return os.path.join(profiles_dir(), f"{safe or 'default'}.json")


def cluster_topics(videos: list[VideoRecord]) -> list[dict[str, Any]]:
    """Group videos into topic clusters and score each one.

    Greedy single-pass agglomeration: a video joins the first cluster it is
    close enough to, otherwise it starts one. Order-dependent by nature, so
    videos are processed newest-first — recent framing defines the cluster,
    not whatever happened to be published first.
    """
    ordered = sorted(
        (v for v in videos if v.published_at),
        key=lambda v: v.published_at,
        reverse=True,
    )
    clusters: list[dict[str, Any]] = []

    for video in ordered:
        tokens = lx.content_tokens(f"{video.topic} {video.title}")
        if not tokens:
            continue
        placed = False
        for cluster in clusters:
            if lx.jaccard(tokens, cluster["tokens"]) >= CLUSTER_THRESHOLD:
                cluster["tokens"] |= tokens
                cluster["videos"].append(video)
                placed = True
                break
        if not placed:
            clusters.append({"tokens": set(tokens), "videos": [video]})

    channel_median = norm.median(v.views for v in videos if v.views is not None)
    summaries: list[dict[str, Any]] = []
    for cluster in clusters:
        members = cluster["videos"]
        if len(members) < MIN_CLUSTER_SIZE:
            continue
        views = [v.views for v in members if v.views is not None]
        cluster_median = norm.median(views)
        summaries.append({
            "label": _cluster_label(members),
            "size": len(members),
            "median_views": cluster_median,
            "performance_vs_channel": (
                round(cluster_median / channel_median, 3)
                if cluster_median and channel_median else None
            ),
            "examples": [v.title for v in members[:3]],
        })

    summaries.sort(
        key=lambda item: (item["performance_vs_channel"] or 0, item["size"]),
        reverse=True,
    )
    return summaries


def _cluster_label(videos: list[VideoRecord]) -> str:
    """Name a cluster by the words that recur across its members."""
    counter: Counter[str] = Counter()
    for video in videos:
        counter.update(lx.content_tokens(f"{video.topic} {video.title}"))
    common = [word for word, count in counter.most_common(3) if count > 1]
    return " / ".join(common) if common else "misc"


def _hour_performance(videos: list[VideoRecord]) -> list[dict[str, Any]]:
    """Median views by publication hour — never a universal "best time"."""
    buckets: dict[int, list[float]] = {}
    for video in videos:
        if video.published_at and video.views is not None:
            buckets.setdefault(video.published_at.hour, []).append(video.views)
    rows = [
        {"hour": hour, "videos": len(values), "median_views": norm.median(values)}
        for hour, values in sorted(buckets.items())
        if len(values) >= 2
    ]
    rows.sort(key=lambda row: row["median_views"] or 0, reverse=True)
    return rows


def _title_patterns(videos: list[VideoRecord]) -> dict[str, Any]:
    """Which headline shapes this channel's winners tend to use.

    Top and bottom quartile, matching how `features/channel.py` splits
    performance, so the profile describes the same "winner" the model is
    learning from.
    """
    scored = sorted(
        (v for v in videos if v.views is not None), key=lambda v: v.views, reverse=True
    )
    if len(scored) < 8:
        return {}
    cut = max(1, int(round(len(scored) * 0.25)))
    winners, losers = scored[:cut], scored[-cut:]

    def shape(group: list[VideoRecord]) -> dict[str, Any]:
        if not group:
            return {}
        return {
            "count": len(group),
            "median_title_chars": norm.median(float(len(v.title)) for v in group),
            "question_share": round(
                sum(1 for v in group if "?" in v.title or "¿" in v.title) / len(group), 3
            ),
            "number_share": round(
                sum(1 for v in group if lx.has_digit(v.title)) / len(group), 3
            ),
            "curiosity_share": round(
                sum(1 for v in group if lx.count_markers(v.title, lx.CURIOSITY)) / len(group), 3
            ),
        }

    return {"winners": shape(winners), "losers": shape(losers)}


def build_profile(channel: ChannelContext) -> dict[str, Any]:
    """The channel's statistical self-portrait (spec §47)."""
    videos = channel.published()
    baseline = norm.channel_baseline(videos, channel.now)
    durations = [v.duration_seconds for v in videos if v.duration_seconds]

    return {
        "account_id": channel.account_id,
        "nickname": channel.nickname,
        "niche": channel.niche,
        "language": channel.language,
        "built_at": now_utc().isoformat(),
        "videos": len(videos),
        "baseline": {
            key: (round(value, 4) if isinstance(value, float) else value)
            for key, value in baseline.items()
        },
        "momentum": norm.momentum(videos, channel.now),
        "duration_preferences": {
            "median_seconds": norm.median(durations),
            "short_ratio": baseline.get("short_ratio"),
        },
        "topic_clusters": cluster_topics(videos)[:12],
        "title_patterns": _title_patterns(videos),
        "publication_hours": _hour_performance(videos)[:6],
        "retention_coverage": round(
            sum(1 for v in videos if v.avg_percentage_viewed is not None)
            / len(videos), 3
        ) if videos else 0.0,
    }


def save_profile(profile: dict[str, Any]) -> str:
    path = profile_path(str(profile.get("account_id") or ""))
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as file:
        json.dump(profile, file, ensure_ascii=False, indent=2, default=str)
    os.replace(tmp_path, path)
    return path


def load_profile(account_id: str) -> dict[str, Any]:
    try:
        with open(profile_path(account_id), "r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def refresh_profiles(channels: list[ChannelContext] | None = None) -> list[str]:
    """Rebuild and persist every channel profile. Returns the paths written."""
    from ..data import collector

    channels = channels if channels is not None else collector.load_channels()
    return [save_profile(build_profile(channel)) for channel in channels]


def briefing(account_id: str, limit: int = 5) -> str:
    """The profile as a short prompt block for the generation layer.

    This is the bridge from statistical memory back into the creative loop:
    facts the LLM should know before it invents the next topic, phrased so
    they can be pasted straight into a prompt.
    """
    profile = load_profile(account_id)
    if not profile:
        return ""

    lines: list[str] = []
    clusters = profile.get("topic_clusters") or []
    winners = [c for c in clusters if (c.get("performance_vs_channel") or 0) > 1.2]
    losers = [c for c in clusters if 0 < (c.get("performance_vs_channel") or 0) < 0.8]

    if winners:
        lines.append(
            "Topic families that over-perform here: "
            + "; ".join(
                f"{c['label']} ({c['performance_vs_channel']:.1f}x)"
                for c in winners[:limit]
            )
        )
    if losers:
        lines.append(
            "Topic families that under-perform here: "
            + "; ".join(
                f"{c['label']} ({c['performance_vs_channel']:.1f}x)"
                for c in losers[:limit]
            )
        )

    patterns = (profile.get("title_patterns") or {}).get("winners") or {}
    if patterns.get("count"):
        lines.append(
            f"Winning titles here average {patterns.get('median_title_chars', 0):.0f} "
            f"characters; {patterns.get('number_share', 0):.0%} contain a number and "
            f"{patterns.get('curiosity_share', 0):.0%} open a curiosity gap."
        )

    hours = profile.get("publication_hours") or []
    if hours:
        best = hours[0]
        lines.append(
            f"Best-performing publication hour so far: {best['hour']:02d}:00 "
            f"({best['videos']} videos)."
        )
    return "\n".join(lines)
