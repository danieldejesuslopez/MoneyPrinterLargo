"""The feature registry: one ordered list, one hash, one vectorizer.

Everything that turns a `Candidate` into numbers goes through here, so:

  - the column order is defined in exactly one place and is stable across
    training and inference;
  - `SCHEMA_HASH` fingerprints that order, and a model trained against a
    different hash is refused rather than mis-fed (see `models.registry`);
  - only `PRE_PUBLISH` features can reach a model input, mechanically.

Adding a feature means appending a `FeatureSpec` to its group module and
bumping `FEATURE_SCHEMA_VERSION`. Nothing else in the package changes.
"""

from __future__ import annotations

from typing import Iterable

from ..constants import FEATURE_SCHEMA_VERSION
from ..schema import Candidate, ChannelContext
from . import channel, plan, script, temporal, text, thumbnail
from .base import (
    POST_PUBLISH,
    PRE_PUBLISH,
    FeatureSpec,
    FeatureVector,
    schema_hash,
)

#: Group order is the column order. Cheap groups first so a partial failure
#: costs the least, and so profiling reads naturally.
ALL_SPECS: tuple[FeatureSpec, ...] = (
    text.SPECS + script.SPECS + plan.SPECS + temporal.SPECS
    + channel.SPECS + thumbnail.SPECS
)


def _assert_unique(specs: Iterable[FeatureSpec]) -> None:
    seen: set[str] = set()
    for spec in specs:
        if spec.name in seen:
            raise ValueError(f"Duplicate feature name: {spec.name}")
        if spec.availability not in (PRE_PUBLISH, POST_PUBLISH):
            raise ValueError(
                f"Feature {spec.name} has unknown availability {spec.availability!r}"
            )
        seen.add(spec.name)


_assert_unique(ALL_SPECS)

#: The features a model is allowed to see.
INPUT_SPECS: tuple[FeatureSpec, ...] = tuple(
    spec for spec in ALL_SPECS if spec.availability == PRE_PUBLISH
)

FEATURE_NAMES: tuple[str, ...] = tuple(spec.name for spec in INPUT_SPECS)
SCHEMA_HASH: str = schema_hash(FEATURE_NAMES)
FEATURE_COUNT: int = len(FEATURE_NAMES)

SPEC_BY_NAME: dict[str, FeatureSpec] = {spec.name: spec for spec in ALL_SPECS}


def vectorize(candidate: Candidate, context: ChannelContext) -> FeatureVector:
    """Extract the full input vector for one candidate.

    Missing values are filled with the spec's documented default *and*
    recorded in `missing`, because "we could not measure this" and "this
    measured zero" must not look the same to the confidence calculation.
    """
    values: list[float] = []
    missing: list[str] = []
    for spec in INPUT_SPECS:
        value = spec.extract(candidate, context)
        if value is None:
            values.append(spec.default)
            missing.append(spec.name)
        else:
            values.append(value)
    return FeatureVector(names=list(FEATURE_NAMES), values=values, missing=missing)


def vectorize_many(
    candidates: list[Candidate], context: ChannelContext
) -> list[FeatureVector]:
    """Batch extraction against one shared context (spec §121).

    The channel group memoizes per context, so N candidates cost one history
    scan rather than N.
    """
    return [vectorize(candidate, context) for candidate in candidates]


def group_of(name: str) -> str:
    spec = SPEC_BY_NAME.get(name)
    return spec.group if spec else ""


def describe(name: str) -> str:
    spec = SPEC_BY_NAME.get(name)
    return spec.description if spec else ""


def manifest() -> dict[str, object]:
    """Serializable description of the feature schema, stored with models."""
    return {
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "schema_hash": SCHEMA_HASH,
        "feature_count": FEATURE_COUNT,
        "features": [
            {
                "name": spec.name,
                "group": spec.group,
                "default": spec.default,
                "description": spec.description,
            }
            for spec in INPUT_SPECS
        ],
    }
