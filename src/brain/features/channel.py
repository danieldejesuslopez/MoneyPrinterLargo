"""Channel-context and novelty features (spec §22, §33, §50, §84).

Two questions this group answers about a candidate:

  - *What is normal here?* Size, cadence, typical retention, momentum. The
    same topic is a different bet on a channel that posts daily to 50k subs
    than on one that posted twice last quarter.
  - *Has this channel already done this?* Similarity against prior topics
    (novelty), against the last few uploads (burn risk), and against the
    channel's own winners and losers (success/failure memory).

Every lookup goes through `ChannelContext.before(moment)`, so a feature can
only ever see videos published before the candidate would have been. That is
the leakage guarantee at the channel level; `features/base.py` enforces the
video level.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from ..data import normalizer as norm
from ..schema import Candidate, ChannelContext, VideoRecord
from . import lexicon as lx
from .base import PRE_PUBLISH, FeatureSpec

#: Videos considered when deciding what a "winner" looks like here.
SUCCESS_WINDOW = 40
#: Uploads that count as "recent" for burn-risk similarity.
RECENT_WINDOW = 10
#: Share of the window that counts as over- and under-performing.
PERFORMANCE_QUANTILE = 0.25
#: Below this much history, quartiles are noise rather than a pattern.
MIN_SPLIT_HISTORY = 8


def moment_of(candidate: Candidate, context: ChannelContext) -> _dt.datetime:
    """When this candidate would go live — the cut-off for all history."""
    return candidate.planned_publish_at or context.now


def _cache(context: ChannelContext) -> dict[str, Any]:
    """Per-context memo so ranking 100 candidates rebuilds nothing 100 times."""
    store = getattr(context, "_brain_cache", None)
    if store is None:
        store = {}
        object.__setattr__(context, "_brain_cache", store)
    return store


def baseline(candidate: Candidate, context: ChannelContext) -> dict[str, Any]:
    moment = moment_of(candidate, context)
    key = f"baseline:{moment.isoformat()}"
    store = _cache(context)
    if key not in store:
        store[key] = norm.channel_baseline(context.published(), moment)
    return store[key]


def _history(candidate: Candidate, context: ChannelContext) -> list[VideoRecord]:
    moment = moment_of(candidate, context)
    key = f"history:{moment.isoformat()}"
    store = _cache(context)
    if key not in store:
        prior = context.before(moment)
        prior.sort(key=lambda v: v.published_at, reverse=True)
        store[key] = prior
    return store[key]


def _topic_tokens(video: VideoRecord) -> set[str]:
    return lx.content_tokens(f"{video.topic} {video.title}")


def _candidate_tokens(candidate: Candidate) -> set[str]:
    return lx.content_tokens(f"{candidate.topic} {candidate.title}")


def _performance_split(
    candidate: Candidate, context: ChannelContext
) -> tuple[list[set[str]], list[set[str]]]:
    """Token sets of the channel's winners and losers, prior to the candidate.

    Split by quartile *within the same window*, not by a fixed multiple of
    the median. Two reasons: a channel that has grown does not classify its
    early history as failure, and a fixed multiple silently produces empty
    sets on a channel whose results are tightly clustered — which would turn
    both features off exactly where they were supposed to discriminate.
    """
    key = "split:" + moment_of(candidate, context).isoformat()
    store = _cache(context)
    if key in store:
        return store[key]

    prior = [v for v in _history(candidate, context) if v.views is not None]
    prior = prior[:SUCCESS_WINDOW]
    winners: list[set[str]] = []
    losers: list[set[str]] = []

    if len(prior) >= MIN_SPLIT_HISTORY:
        ranked = sorted(prior, key=lambda v: v.views, reverse=True)
        cut = max(1, int(round(len(ranked) * PERFORMANCE_QUANTILE)))
        winners = [tokens for tokens in map(_topic_tokens, ranked[:cut]) if tokens]
        losers = [tokens for tokens in map(_topic_tokens, ranked[-cut:]) if tokens]

    store[key] = (winners, losers)
    return winners, losers


def _max_similarity(tokens: set[str], others: list[set[str]]) -> float | None:
    if not tokens or not others:
        return None
    return max(lx.jaccard(tokens, other) for other in others)


# -- Channel shape ------------------------------------------------------


def channel_history_count(c: Candidate, ctx: ChannelContext) -> float | None:
    return float(baseline(c, ctx)["count"])


def channel_median_views_log(c: Candidate, ctx: ChannelContext) -> float | None:
    """Log of the channel's median views — the scale the label is relative to.

    Included so the model can learn that what works on a small channel is
    not what works on a large one, without the raw number's huge range
    swamping every other feature.
    """
    import math

    value = baseline(c, ctx)["median_views"]
    return math.log1p(value) if value is not None else None


def channel_median_retention(c: Candidate, ctx: ChannelContext) -> float | None:
    value = baseline(c, ctx)["median_retention"]
    return value / 100.0 if value is not None else None


def channel_days_since_last_upload(c: Candidate, ctx: ChannelContext) -> float | None:
    return baseline(c, ctx)["days_since_last_upload"]


def channel_uploads_last_7d(c: Candidate, ctx: ChannelContext) -> float | None:
    return float(baseline(c, ctx)["uploads_last_7d"])


def channel_uploads_last_30d(c: Candidate, ctx: ChannelContext) -> float | None:
    return float(baseline(c, ctx)["uploads_last_30d"])


def channel_short_ratio(c: Candidate, ctx: ChannelContext) -> float | None:
    return baseline(c, ctx)["short_ratio"]


def channel_age_days(c: Candidate, ctx: ChannelContext) -> float | None:
    return baseline(c, ctx)["channel_age_days"]


def channel_momentum(c: Candidate, ctx: ChannelContext) -> float | None:
    return norm.momentum(ctx.published(), moment_of(c, ctx))


def channel_format_level(c: Candidate, ctx: ChannelContext) -> float | None:
    """How this format has performed here, relative to the channel overall.

    The single most useful thing to know before predicting a video on a
    small channel, and the model was being asked to infer it from a binary
    flag. Savirox's Shorts run at roughly fifty times its long-form; a
    linear model given only `kind_is_long` has to spend one coefficient
    spanning that gap for every candidate, which it cannot do well.

    Handing over the format's own historical level turns the strongest
    trivial baseline into an input: the model starts from the right level
    and spends its capacity on the question that is actually open, which is
    which video *within* a format will do better.

    Computed from prior videos only, like every other channel feature, so
    it carries no information the candidate's own result could leak.
    """
    prior = [v for v in _history(c, ctx) if v.views is not None]
    if len(prior) < 6:
        return None
    is_short = c.kind != "long"
    same = [v.views for v in prior if v.is_short == is_short]
    if len(same) < 3:
        return None
    overall = norm.median(v.views for v in prior)
    level = norm.median(same)
    if not overall or not level:
        return None
    import math

    # Log ratio: symmetric around 0, and bounded enough that a format with
    # fifty times the reach does not swamp every other feature.
    return math.log((level + 1.0) / (overall + 1.0))


def channel_format_share(c: Candidate, ctx: ChannelContext) -> float | None:
    """Share of prior uploads that used this candidate's format."""
    prior = _history(c, ctx)
    if len(prior) < 6:
        return None
    is_short = c.kind != "long"
    return sum(1 for v in prior if v.is_short == is_short) / len(prior)


def kind_matches_channel(c: Candidate, ctx: ChannelContext) -> float | None:
    """Is this the format the channel usually publishes?"""
    ratio = baseline(c, ctx)["short_ratio"]
    if ratio is None:
        return None
    is_short = c.kind != "long"
    return ratio if is_short else 1.0 - ratio


# -- Novelty and memory -------------------------------------------------


def topic_novelty(c: Candidate, ctx: ChannelContext) -> float | None:
    """1 - closest match against everything this channel already published.

    The pipeline already *rejects* exact duplicates; this grades the degree,
    so "another black-hole video, the fourth this month" ranks below a fresh
    angle instead of merely passing the duplicate gate (spec §33).
    """
    tokens = _candidate_tokens(c)
    if not tokens:
        return None
    similarity = _max_similarity(tokens, [_topic_tokens(v) for v in _history(c, ctx)])
    return None if similarity is None else 1.0 - similarity


def topic_recent_similarity(c: Candidate, ctx: ChannelContext) -> float | None:
    """Closest match against the last few uploads — near-term burn risk."""
    tokens = _candidate_tokens(c)
    if not tokens:
        return None
    recent = _history(c, ctx)[:RECENT_WINDOW]
    return _max_similarity(tokens, [_topic_tokens(v) for v in recent])


def topic_winner_similarity(c: Candidate, ctx: ChannelContext) -> float | None:
    """Closest match against the channel's over-performers (spec §50)."""
    winners, _ = _performance_split(c, ctx)
    return _max_similarity(_candidate_tokens(c), winners)


def topic_loser_similarity(c: Candidate, ctx: ChannelContext) -> float | None:
    """Closest match against the channel's under-performers (spec §49)."""
    _, losers = _performance_split(c, ctx)
    return _max_similarity(_candidate_tokens(c), losers)


SPECS: tuple[FeatureSpec, ...] = (
    FeatureSpec("channel_history_count", "channel", channel_history_count,
                PRE_PUBLISH, 0.0, "Videos published before this one"),
    FeatureSpec("channel_median_views_log", "channel", channel_median_views_log,
                PRE_PUBLISH, 0.0, "log1p of the channel's recent median views"),
    FeatureSpec("channel_median_retention", "channel", channel_median_retention,
                PRE_PUBLISH, 0.4, "Channel's median average-percentage-viewed"),
    FeatureSpec("channel_days_since_last_upload", "channel",
                channel_days_since_last_upload, PRE_PUBLISH, 3.0,
                "Days since the previous upload"),
    FeatureSpec("channel_uploads_last_7d", "channel", channel_uploads_last_7d,
                PRE_PUBLISH, 0.0, "Uploads in the previous 7 days"),
    FeatureSpec("channel_uploads_last_30d", "channel", channel_uploads_last_30d,
                PRE_PUBLISH, 0.0, "Uploads in the previous 30 days"),
    FeatureSpec("channel_short_ratio", "channel", channel_short_ratio,
                PRE_PUBLISH, 1.0, "Share of recent uploads that are Shorts"),
    FeatureSpec("channel_age_days", "channel", channel_age_days,
                PRE_PUBLISH, 0.0, "Days since the channel's first upload"),
    FeatureSpec("channel_momentum", "channel", channel_momentum,
                PRE_PUBLISH, 1.0, "Recent median views over the preceding ones"),
    FeatureSpec("kind_matches_channel", "channel", kind_matches_channel,
                PRE_PUBLISH, 0.5, "Format agreement with the channel's habit"),
    FeatureSpec("channel_format_level", "channel", channel_format_level,
                PRE_PUBLISH, 0.0,
                "How this format has performed here, vs the channel overall"),
    FeatureSpec("channel_format_share", "channel", channel_format_share,
                PRE_PUBLISH, 0.5, "Share of prior uploads using this format"),
    FeatureSpec("topic_novelty", "novelty", topic_novelty,
                PRE_PUBLISH, 1.0, "1 - similarity to anything already published"),
    FeatureSpec("topic_recent_similarity", "novelty", topic_recent_similarity,
                PRE_PUBLISH, 0.0, "Similarity to the last few uploads"),
    FeatureSpec("topic_winner_similarity", "novelty", topic_winner_similarity,
                PRE_PUBLISH, 0.0, "Similarity to the channel's over-performers"),
    FeatureSpec("topic_loser_similarity", "novelty", topic_loser_similarity,
                PRE_PUBLISH, 0.0, "Similarity to the channel's under-performers"),
)
