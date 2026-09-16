"""Production-plan features: format, duration, cost, and existing lab scores.

The most valuable features in this group are the ones the repo already
computes. `RetentionLab` scores a script before rendering, picks a hook out
of scored candidates, and grades the first image; every one of those numbers
is written into the video's cache entry at upload time. They are genuine
pre-publish signals produced by a system that has been tuned by hand — so
Brain's first job is not to replace them but to find out, from real results,
how much each one is actually worth (spec §135, §136).

Cost features exist for the same reason: a candidate that scores 4% higher
for triple the render time is usually the wrong call (spec §10).
"""

from __future__ import annotations

import math

from ..schema import Candidate, ChannelContext, to_number
from .base import PRE_PUBLISH, FeatureSpec

#: Duration assumed when a Short candidate carries no explicit target.
DEFAULT_SHORT_SECONDS = 45.0


def kind_is_long(c: Candidate, _: ChannelContext) -> float | None:
    return float(c.kind == "long")


def target_duration_log(c: Candidate, _: ChannelContext) -> float | None:
    duration = c.target_duration_seconds
    if duration is None:
        duration = DEFAULT_SHORT_SECONDS if c.kind != "long" else None
    if not duration or duration <= 0:
        return None
    return math.log1p(duration)


def production_cost(c: Candidate, _: ChannelContext) -> float | None:
    return c.production_cost


def lab_retention_score(c: Candidate, _: ChannelContext) -> float | None:
    """RetentionLab's pre-render script score (0-10)."""
    return c.retention_score


def lab_hook_score(c: Candidate, _: ChannelContext) -> float | None:
    """Score of the hook the lab selected out of its candidate set."""
    return c.hook_score


def lab_first_image_score(c: Candidate, _: ChannelContext) -> float | None:
    """Score of the opening frame — the other half of the first-second bet."""
    return c.first_image_score


def has_thumbnail(c: Candidate, _: ChannelContext) -> float | None:
    return float(bool(c.thumbnail_path))


def is_remix(c: Candidate, _: ChannelContext) -> float | None:
    """Was this derived from an earlier winner by WinnerRemix?"""
    value = c.meta.get("is_remix")
    return None if value is None else float(bool(value))


def retention_mode_is_max(c: Candidate, _: ChannelContext) -> float | None:
    if not c.retention_mode:
        return None
    return float("max" in c.retention_mode.lower())


def hook_variant_count(c: Candidate, _: ChannelContext) -> float | None:
    """How many hooks were generated before one was chosen.

    A proxy for how hard the selection worked: the wider the field the lab
    picked from, the better the winner tends to be.
    """
    return to_number(len(c.meta.get("hook_variants") or []) or None)


SPECS: tuple[FeatureSpec, ...] = (
    FeatureSpec("kind_is_long", "plan", kind_is_long, PRE_PUBLISH, 0.0,
                "Long-form rather than Short"),
    FeatureSpec("target_duration_log", "plan", target_duration_log, PRE_PUBLISH,
                math.log1p(DEFAULT_SHORT_SECONDS), "log1p of target duration"),
    FeatureSpec("production_cost", "plan", production_cost, PRE_PUBLISH, 0.0,
                "Estimated production cost"),
    FeatureSpec("lab_retention_score", "plan", lab_retention_score, PRE_PUBLISH, 0.0,
                "RetentionLab pre-render script score"),
    FeatureSpec("lab_hook_score", "plan", lab_hook_score, PRE_PUBLISH, 0.0,
                "Selected hook's lab score"),
    FeatureSpec("lab_first_image_score", "plan", lab_first_image_score, PRE_PUBLISH,
                0.0, "Opening-frame lab score"),
    FeatureSpec("has_thumbnail", "plan", has_thumbnail, PRE_PUBLISH, 0.0,
                "A custom thumbnail is attached"),
    FeatureSpec("is_remix", "plan", is_remix, PRE_PUBLISH, 0.0,
                "Derived from an earlier winner"),
    FeatureSpec("retention_mode_is_max", "plan", retention_mode_is_max, PRE_PUBLISH,
                0.0, "Rendered in a max-retention mode"),
    FeatureSpec("hook_variant_count", "plan", hook_variant_count, PRE_PUBLISH, 0.0,
                "Hooks generated before selection"),
)
