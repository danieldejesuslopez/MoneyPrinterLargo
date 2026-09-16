"""The multi-head model: one regression head per target, one shared input.

Spec §26 asks for a single backbone feeding CTR, retention, views, watch
time, engagement and subscriber heads. At V0 the "backbone" is the shared
standardized feature vector and each head is its own ridge fit — which gives
the same interface, the same multi-target output, and none of the risk of
training a shared representation on a few dozen rows.

Two things the container adds over a bag of models:

  - **Honesty about what it knows.** A head only exists for a target that
    had enough real labels. `predict` returns nothing for the others rather
    than a confident-looking zero, and the caller falls back to heuristics.
  - **Calibration.** Each head stores the spread of its validation residuals,
    so a prediction can carry a confidence instead of a bare number
    (spec §75). A head that explains nothing reports low confidence and
    Brain abstains (spec §76).
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..constants import BRAIN_VERSION, TIER_HEURISTIC
from .linear import RidgeModel


@dataclass
class MultiTaskModel:
    """A trained Brain: several heads over one feature schema."""

    version: str = ""
    created_at: str = ""
    brain_version: str = BRAIN_VERSION
    feature_schema: str = ""
    feature_names: list[str] = field(default_factory=list)
    tier: str = TIER_HEURISTIC
    heads: dict[str, RidgeModel] = field(default_factory=dict)
    training: dict[str, Any] = field(default_factory=dict)
    calibration: dict[str, dict[str, float]] = field(default_factory=dict)

    # -- inference ------------------------------------------------------

    @property
    def targets(self) -> list[str]:
        return sorted(self.heads)

    def predict(
        self, values: Sequence[float], missing: set[int] | None = None
    ) -> dict[str, float]:
        """Raw head outputs, still in the space each head was fitted in."""
        return {
            name: head.predict(values, missing) for name, head in self.heads.items()
        }

    def contributions(
        self, target: str, values: Sequence[float], missing: set[int] | None = None
    ) -> dict[str, float]:
        head = self.heads.get(target)
        return head.contributions(values, missing) if head else {}

    def confidence(self, target: str) -> float:
        """How much this head actually explains, as a 0-1 number.

        Derived from validation R^2, floored at 0 and damped by sample count:
        a head fitted on 40 rows that scores 0.5 should not claim the same
        confidence as one fitted on 4,000 that scores 0.5.
        """
        stats = self.calibration.get(target) or {}
        r2 = float(stats.get("r2", 0.0) or 0.0)
        samples = float(stats.get("n", 0) or 0)
        if r2 <= 0 or samples <= 0:
            return 0.0
        sample_factor = min(1.0, samples / 200.0)
        return max(0.0, min(1.0, r2 * (0.4 + 0.6 * sample_factor)))

    def head_metrics(self) -> dict[str, dict[str, float]]:
        return {name: dict(stats) for name, stats in self.calibration.items()}

    def is_compatible(self, schema_hash: str) -> bool:
        """Was this model trained against the feature schema now running?"""
        return not self.feature_schema or self.feature_schema == schema_hash

    # -- persistence ----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "created_at": self.created_at or _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "brain_version": self.brain_version,
            "feature_schema": self.feature_schema,
            "feature_names": list(self.feature_names),
            "tier": self.tier,
            "heads": {name: head.to_dict() for name, head in self.heads.items()},
            "training": dict(self.training),
            "calibration": {k: dict(v) for k, v in self.calibration.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MultiTaskModel":
        heads = {
            name: RidgeModel.from_dict(payload)
            for name, payload in (data.get("heads") or {}).items()
            if isinstance(payload, dict)
        }
        return cls(
            version=str(data.get("version") or ""),
            created_at=str(data.get("created_at") or ""),
            brain_version=str(data.get("brain_version") or BRAIN_VERSION),
            feature_schema=str(data.get("feature_schema") or ""),
            feature_names=list(data.get("feature_names") or []),
            tier=str(data.get("tier") or TIER_HEURISTIC),
            heads=heads,
            training=dict(data.get("training") or {}),
            calibration={
                str(k): {str(mk): float(mv) for mk, mv in (v or {}).items()}
                for k, v in (data.get("calibration") or {}).items()
            },
        )

    def summary(self) -> dict[str, Any]:
        """Compact description for `brain status` and the dashboard."""
        return {
            "version": self.version,
            "created_at": self.created_at,
            "tier": self.tier,
            "feature_schema": self.feature_schema,
            "targets": self.targets,
            "samples": self.training.get("samples"),
            "head_confidence": {
                target: round(self.confidence(target), 3) for target in self.targets
            },
        }
