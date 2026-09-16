"""Feature primitives: the spec type and the availability contract.

The one rule this file exists to enforce: **a feature must declare when it
becomes knowable**. `PRE_PUBLISH` features can be computed for a candidate
that has never been uploaded; `POST_PUBLISH` features need results that only
exist afterwards.

Only `PRE_PUBLISH` features are ever vectorized into X. That is what makes
the leakage test in `tests/brain/test_no_leakage.py` a mechanical guarantee
rather than a promise: adding a feature that peeks at `views` is impossible
unless you also mark it post-publish, and marking it post-publish removes it
from the model input.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Callable

from ..schema import Candidate, ChannelContext

#: Knowable before the video exists. Eligible for model input.
PRE_PUBLISH = "pre_publish"
#: Only knowable after publication. Labels and diagnostics only.
POST_PUBLISH = "post_publish"

Extractor = Callable[[Candidate, ChannelContext], "float | None"]


@dataclass(frozen=True)
class FeatureSpec:
    """One scalar input to the model."""

    name: str
    group: str
    fn: Extractor
    availability: str = PRE_PUBLISH
    default: float = 0.0
    description: str = ""

    def extract(self, candidate: Candidate, context: ChannelContext) -> float | None:
        """Run the extractor, converting any failure into "unknown".

        A broken extractor must degrade one feature, never a whole ranking
        run — Brain is called from inside the generation pipeline.
        """
        try:
            value = self.fn(candidate, context)
        except Exception:
            return None
        if value is None or isinstance(value, bool):
            return None if value is None else float(value)
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return None if number != number else number


@dataclass
class FeatureVector:
    """Extracted features plus how much of the candidate was actually known."""

    names: list[str] = field(default_factory=list)
    values: list[float] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        """Share of features that came back with a real value (0-1)."""
        if not self.names:
            return 0.0
        return 1.0 - (len(self.missing) / len(self.names))

    def as_dict(self) -> dict[str, float]:
        return dict(zip(self.names, self.values))


def schema_hash(names: list[str] | tuple[str, ...]) -> str:
    """Fingerprint of the feature vector's shape.

    Stored beside every trained model. A model whose hash no longer matches
    the running code is refused rather than silently fed a vector whose
    columns have shifted underneath it.
    """
    payload = "|".join(names)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def safe_ratio(numerator: Any, denominator: Any, default: float | None = None) -> float | None:
    """`numerator / denominator`, or `default` when that is meaningless."""
    try:
        num = float(numerator)
        den = float(denominator)
    except (TypeError, ValueError):
        return default
    if den == 0:
        return default
    return num / den
