"""Scoring one candidate: blend, calibrate, and say how sure you are.

The final score is a weighted blend of four independent opinions (spec §137):

  - **neural** — the trained heads, converted to a reward and scaled 0-10.
  - **heuristic** — Brain's own rule table, which works from row zero.
  - **rules** — the scores `RetentionLab` already produced for this
    candidate. They are kept as a separate voice rather than folded in,
    because they come from a system that was tuned by hand against these
    channels and should not be silently outvoted by a model fitted on
    fifty rows.
  - **novelty** — distance from what the channel already published.

Components that cannot be computed drop out and the remaining weights are
renormalized, so a topic-stage candidate with no script and no model still
gets a meaningful score, just with lower confidence.

Confidence is not decoration. Below the configured floor, `should_decide`
returns False and the caller is expected to leave the decision to the
existing pipeline (spec §76).
"""

from __future__ import annotations

import math
from typing import Any, Sequence

from ..config import get_abstain_confidence, get_hybrid_weights, get_reward_weights
from ..constants import (
    SUCCESS_CLASSES,
    TARGET_NORMALIZED_VIEWS,
    TIER_HEURISTIC,
    tier_for_samples,
)
from ..data import dataset as ds
from ..data import normalizer as norm
from ..features import registry as fr
from ..features.base import FeatureVector
from ..models import heuristic
from ..models import registry as model_registry
from ..models.multitask import MultiTaskModel
from ..schema import BrainPrediction, Candidate, ChannelContext, Explanation
from .explainer import build_explanation

#: Ceiling on confidence when no trained model contributed. Rules encode
#: plausible beliefs, not measured ones.
HEURISTIC_CONFIDENCE_CAP = 0.5


class Predictor:
    """Loads the champion once and scores candidates against it (spec §120).

    Construct one per run and reuse it: model loading and channel history
    scans are the expensive parts, and both are cached here.
    """

    def __init__(
        self,
        account_id: str = "",
        model: MultiTaskModel | None = None,
        load_model: bool = True,
    ):
        self.account_id = account_id
        self.reward_weights = get_reward_weights(account_id)
        self.hybrid_weights = get_hybrid_weights(account_id)
        self.abstain_below = get_abstain_confidence(account_id)
        self.model = model if model is not None else (
            model_registry.champion() if load_model else None
        )
        self.notes: list[str] = []

        if self.model and not self.model.is_compatible(fr.SCHEMA_HASH):
            self.notes.append(
                f"Model {self.model.version} was trained against feature schema "
                f"{self.model.feature_schema}, running schema is {fr.SCHEMA_HASH}. "
                "Ignoring it and scoring heuristically until it is retrained."
            )
            self.model = None

    # -- public API -----------------------------------------------------

    @property
    def tier(self) -> str:
        if not self.model:
            return TIER_HEURISTIC
        samples = self.model.training.get("samples")
        try:
            return tier_for_samples(int(samples))
        except (TypeError, ValueError):
            return self.model.tier or TIER_HEURISTIC

    @property
    def model_version(self) -> str:
        return self.model.version if self.model else "heuristic"

    def predict(
        self, candidate: Candidate, context: ChannelContext, explain: bool = True
    ) -> BrainPrediction:
        vector = fr.vectorize(candidate, context)
        return self._predict_from_vector(candidate, vector, explain)

    def predict_many(
        self,
        candidates: Sequence[Candidate],
        context: ChannelContext,
        explain: bool = False,
    ) -> list[BrainPrediction]:
        """Batch scoring — one history scan for the whole set (spec §121)."""
        vectors = fr.vectorize_many(list(candidates), context)
        return [
            self._predict_from_vector(candidate, vector, explain)
            for candidate, vector in zip(candidates, vectors)
        ]

    def should_decide(self, prediction: BrainPrediction) -> bool:
        """Is Brain sure enough to act on this rather than abstain?"""
        return prediction.confidence >= self.abstain_below

    # -- internals ------------------------------------------------------

    def _predict_from_vector(
        self, candidate: Candidate, vector: FeatureVector, explain: bool
    ) -> BrainPrediction:
        features = vector.as_dict()
        missing = set(vector.missing)
        # Index form of the same set: the model imputes these to its own
        # training mean so an unmeasured feature contributes nothing.
        missing_indices = {
            index for index, name in enumerate(vector.names) if name in missing
        }

        heuristic_score, heuristic_factors = heuristic.score(features, missing)
        targets = self._model_targets(vector.values, missing_indices)
        neural_score = self._neural_score(targets)
        rules_score = self._lab_score(candidate)
        novelty = None if "topic_novelty" in missing else features.get("topic_novelty")

        components: dict[str, float] = {}
        if neural_score is not None:
            components["neural"] = neural_score
        components["heuristic"] = heuristic_score
        if rules_score is not None:
            components["rules"] = rules_score
        if novelty is not None:
            components["novelty"] = 10.0 * novelty

        score = self._blend(components)
        confidence = self._confidence(vector, bool(targets))
        success = self._success_distribution(targets)

        explanation: Explanation | None = None
        if explain:
            explanation = build_explanation(
                score=score,
                confidence=confidence,
                heuristic_factors=heuristic_factors,
                model=self.model,
                feature_values=vector.values,
                feature_names=vector.names,
                missing_indices=missing_indices,
                components=components,
                notes=list(self.notes),
            )

        return BrainPrediction(
            candidate_id=candidate.candidate_id,
            score=score,
            confidence=confidence,
            targets=targets,
            success_probabilities=success,
            tier=self.tier,
            model_version=self.model_version,
            source="hybrid" if neural_score is not None else "heuristic",
            novelty=novelty,
            explanation=explanation,
        )

    def _model_targets(
        self, values: Sequence[float], missing: set[int] | None = None
    ) -> dict[str, float]:
        """Head outputs converted back into real units."""
        if not self.model:
            return {}
        raw = self.model.predict(values, missing)
        return {
            target: ds.inverse_target(target, value)
            for target, value in raw.items()
        }

    def _neural_score(self, targets: dict[str, float]) -> float | None:
        if not targets:
            return None
        reward = norm.reward(targets, self.reward_weights)
        return None if reward is None else 10.0 * max(0.0, min(1.0, reward))

    @staticmethod
    def _lab_score(candidate: Candidate) -> float | None:
        """Average of whichever RetentionLab scores this candidate carries."""
        values = [
            value for value in (
                candidate.retention_score,
                candidate.hook_score,
                candidate.first_image_score,
            ) if value is not None
        ]
        return sum(values) / len(values) if values else None

    def _blend(self, components: dict[str, float]) -> float:
        weights = {
            name: self.hybrid_weights.get(name, 0.0) for name in components
        }
        total = sum(weights.values())
        if total <= 0:
            # Every available component has zero configured weight; fall
            # back to a plain average rather than returning a hard zero.
            return sum(components.values()) / len(components)
        score = sum(components[name] * weights[name] for name in components) / total
        return max(0.0, min(10.0, score))

    def _confidence(self, vector: FeatureVector, has_model: bool) -> float:
        """Blend of what the model explains and how much we could measure."""
        coverage = vector.coverage
        if not has_model or not self.model:
            return min(HEURISTIC_CONFIDENCE_CAP, 0.15 + 0.5 * coverage)

        weighted = 0.0
        total_weight = 0.0
        for target in self.model.targets:
            weight = self.reward_weights.get(target, 0.0)
            if weight <= 0:
                continue
            weighted += weight * self.model.confidence(target)
            total_weight += weight
        model_confidence = weighted / total_weight if total_weight else 0.0
        return max(0.0, min(1.0, 0.6 * model_confidence + 0.4 * coverage))

    def _success_distribution(self, targets: dict[str, float]) -> dict[str, float]:
        """Probability of landing in each success band (spec §29).

        Uses the view head's own validation residual spread as the width of
        a log-normal around the prediction. With no head, or no measured
        residual, it returns nothing rather than a made-up distribution.
        """
        predicted = targets.get(TARGET_NORMALIZED_VIEWS)
        if predicted is None or not self.model:
            return {}
        stats = self.model.calibration.get(TARGET_NORMALIZED_VIEWS) or {}
        sigma = float(stats.get("residual_std", 0.0) or 0.0)
        if sigma <= 0:
            return {}

        mu = math.log1p(max(0.0, predicted))
        bands = list(SUCCESS_CLASSES) + [("__top__", float("inf"))]
        distribution: dict[str, float] = {}
        for index in range(len(SUCCESS_CLASSES)):
            name, low = SUCCESS_CLASSES[index]
            high = bands[index + 1][1]
            lower = _normal_cdf(math.log1p(low), mu, sigma)
            upper = 1.0 if math.isinf(high) else _normal_cdf(math.log1p(high), mu, sigma)
            probability = max(0.0, upper - lower)
            if probability > 0.0005:
                distribution[name] = probability
        total = sum(distribution.values()) or 1.0
        return {name: value / total for name, value in distribution.items()}


def _normal_cdf(value: float, mean: float, std: float) -> float:
    if std <= 0:
        return 0.0 if value < mean else 1.0
    return 0.5 * (1.0 + math.erf((value - mean) / (std * math.sqrt(2.0))))


def predict(
    candidate: Candidate,
    context: ChannelContext,
    account_id: str = "",
) -> BrainPrediction:
    """One-shot convenience wrapper — builds a `Predictor` per call.

    Fine for a single candidate; use a `Predictor` instance when scoring a
    set, so the model and channel history are loaded once.
    """
    return Predictor(account_id or context.account_id).predict(candidate, context)


def status(account_id: str = "") -> dict[str, Any]:
    """What Brain currently is: model, tier, and what it can predict."""
    predictor = Predictor(account_id)
    return {
        "model_version": predictor.model_version,
        "tier": predictor.tier,
        "targets": predictor.model.targets if predictor.model else [],
        "reward_weights": predictor.reward_weights,
        "hybrid_weights": predictor.hybrid_weights,
        "abstain_below": predictor.abstain_below,
        "notes": predictor.notes,
    }
