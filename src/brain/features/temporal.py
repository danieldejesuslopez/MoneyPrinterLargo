"""Publication-timing features (spec §24).

Hour and weekday are encoded as sine/cosine pairs rather than raw numbers so
that 23:00 sits next to 00:00 instead of at the opposite end of the range —
otherwise a linear model has to spend two coefficients learning that midnight
is not the furthest possible moment from 11pm.

There is deliberately no "best hour" constant anywhere in this package. The
right slot is a per-channel, per-audience fact the model learns from that
channel's own history.
"""

from __future__ import annotations

import math

from ..schema import Candidate, ChannelContext
from .base import PRE_PUBLISH, FeatureSpec
from .channel import moment_of


def _cyclic(value: float, period: float) -> tuple[float, float]:
    angle = 2.0 * math.pi * (value % period) / period
    return math.sin(angle), math.cos(angle)


def publish_hour_sin(c: Candidate, ctx: ChannelContext) -> float | None:
    return _cyclic(moment_of(c, ctx).hour, 24.0)[0]


def publish_hour_cos(c: Candidate, ctx: ChannelContext) -> float | None:
    return _cyclic(moment_of(c, ctx).hour, 24.0)[1]


def publish_dow_sin(c: Candidate, ctx: ChannelContext) -> float | None:
    return _cyclic(moment_of(c, ctx).weekday(), 7.0)[0]


def publish_dow_cos(c: Candidate, ctx: ChannelContext) -> float | None:
    return _cyclic(moment_of(c, ctx).weekday(), 7.0)[1]


def publish_is_weekend(c: Candidate, ctx: ChannelContext) -> float | None:
    return float(moment_of(c, ctx).weekday() >= 5)


def publish_month_sin(c: Candidate, ctx: ChannelContext) -> float | None:
    return _cyclic(moment_of(c, ctx).month - 1, 12.0)[0]


def publish_month_cos(c: Candidate, ctx: ChannelContext) -> float | None:
    return _cyclic(moment_of(c, ctx).month - 1, 12.0)[1]


SPECS: tuple[FeatureSpec, ...] = (
    FeatureSpec("publish_hour_sin", "temporal", publish_hour_sin, PRE_PUBLISH, 0.0,
                "Publication hour (cyclic)"),
    FeatureSpec("publish_hour_cos", "temporal", publish_hour_cos, PRE_PUBLISH, 1.0,
                "Publication hour (cyclic)"),
    FeatureSpec("publish_dow_sin", "temporal", publish_dow_sin, PRE_PUBLISH, 0.0,
                "Weekday (cyclic)"),
    FeatureSpec("publish_dow_cos", "temporal", publish_dow_cos, PRE_PUBLISH, 1.0,
                "Weekday (cyclic)"),
    FeatureSpec("publish_is_weekend", "temporal", publish_is_weekend, PRE_PUBLISH, 0.0,
                "Published on a Saturday or Sunday"),
    FeatureSpec("publish_month_sin", "temporal", publish_month_sin, PRE_PUBLISH, 0.0,
                "Month of year (cyclic)"),
    FeatureSpec("publish_month_cos", "temporal", publish_month_cos, PRE_PUBLISH, 1.0,
                "Month of year (cyclic)"),
)
