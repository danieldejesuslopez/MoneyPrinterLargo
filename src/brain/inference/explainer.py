"""Why this score — and what would change it.

Two kinds of explanation, both exact rather than approximated:

  - **Attribution.** A ridge model's prediction decomposes into per-feature
    contributions that sum to the prediction, so `WHY?` is arithmetic, not
    SHAP sampling. The rule table's factors are already contributions by
    construction. Both are merged into one ranked list (spec §44, §45).
  - **Counterfactuals.** Re-score modified copies of the candidate and
    report the deltas, which answers "what would I change?" instead of
    "what correlated with this?" (spec §46).

The wording aims at a person reading a scorecard, so factors carry the
feature's own description rather than its variable name.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Sequence

from ..constants import TARGET_NORMALIZED_VIEWS
from ..features import registry as fr
from ..models.multitask import MultiTaskModel
from ..schema import BrainPrediction, Candidate, ChannelContext, Explanation, Factor

#: How many factors of each sign to surface.
TOP_FACTORS = 5


def build_explanation(
    score: float,
    confidence: float,
    heuristic_factors: list[Factor],
    model: MultiTaskModel | None,
    feature_values: Sequence[float],
    feature_names: Sequence[str],
    components: dict[str, float],
    notes: list[str] | None = None,
    missing_indices: set[int] | None = None,
) -> Explanation:
    """Merge rule factors and model attributions into one ranked story."""
    factors: list[Factor] = list(heuristic_factors)
    factors.extend(
        _model_factors(model, feature_values, feature_names, missing_indices)
    )

    positive = sorted(
        (f for f in factors if f.contribution > 0),
        key=lambda f: f.contribution,
        reverse=True,
    )[:TOP_FACTORS]
    negative = sorted(
        (f for f in factors if f.contribution < 0), key=lambda f: f.contribution
    )[:TOP_FACTORS]

    explanation_notes = list(notes or [])
    if components:
        explanation_notes.append(
            "components: "
            + ", ".join(
                f"{name} {value:.1f}" for name, value in sorted(components.items())
            )
        )
    return Explanation(
        score=score,
        confidence=confidence,
        positive_factors=positive,
        negative_factors=negative,
        notes=explanation_notes,
    )


def _model_factors(
    model: MultiTaskModel | None,
    values: Sequence[float],
    names: Sequence[str],
    missing: set[int] | None = None,
) -> list[Factor]:
    """Per-feature attributions from the head that drives the score most.

    Only the leading head is explained. Listing every head's attribution for
    every feature produces a wall of numbers that nobody reads; the view
    head is the one the ranking is dominated by.
    """
    if not model or not model.heads:
        return []
    target = (
        TARGET_NORMALIZED_VIEWS
        if TARGET_NORMALIZED_VIEWS in model.heads
        else model.targets[0]
    )
    contributions = model.contributions(target, values, missing)
    if not contributions:
        return []

    largest = max(abs(value) for value in contributions.values()) or 1.0
    factors: list[Factor] = []
    for name, contribution in contributions.items():
        # Scale attributions into the same rough range as rule points so the
        # merged ranking is not dominated by whichever happens to use the
        # larger units.
        scaled = 1.5 * contribution / largest
        if abs(scaled) < 0.05:
            continue
        # Marked "learned" so a reader can tell a measured association from
        # a rule. Without it a line like "distance from everything the
        # channel already published" in the negative column reads as a
        # judgement about novelty, when it is really this channel's data
        # saying novelty did not pay here.
        description = fr.describe(name) or name
        factors.append(
            Factor(
                name=name,
                contribution=scaled,
                detail=f"learned: {description[0].lower()}{description[1:]}",
            )
        )
    return factors


def explain_prediction(prediction: BrainPrediction) -> dict[str, Any]:
    """Serializable `WHY?` payload for the CLI and the dashboard (spec §70)."""
    explanation = prediction.explanation
    return {
        "score": round(prediction.score, 2),
        "confidence": round(prediction.confidence, 2),
        "tier": prediction.tier,
        "model_version": prediction.model_version,
        "targets": {k: round(v, 4) for k, v in prediction.targets.items()},
        "success_probabilities": {
            k: round(v, 3) for k, v in prediction.success_probabilities.items()
        },
        "positive": [
            {"factor": f.detail or f.name, "weight": round(f.contribution, 3)}
            for f in (explanation.positive_factors if explanation else [])
        ],
        "negative": [
            {"factor": f.detail or f.name, "weight": round(f.contribution, 3)}
            for f in (explanation.negative_factors if explanation else [])
        ],
        "notes": list(explanation.notes) if explanation else [],
    }


# -- Counterfactuals ----------------------------------------------------


def variant(candidate: Candidate, **changes: Any) -> Candidate:
    """A copy of `candidate` with some fields replaced."""
    return dataclasses.replace(candidate, **changes)


def counterfactuals(
    predictor,
    candidate: Candidate,
    context: ChannelContext,
    variants: dict[str, Candidate],
) -> dict[str, Any]:
    """Score alternatives against the original and report the deltas.

    `variants` maps a label ("title v2", "shorter") to a modified candidate.
    The answer is the useful one: not "your hook is weak" but "this hook is
    worth +0.9 and the title change is worth +0.1, so fix the hook".
    """
    baseline = predictor.predict(candidate, context, explain=False)
    results = []
    for label, alternative in variants.items():
        prediction = predictor.predict(alternative, context, explain=False)
        results.append({
            "label": label,
            "score": round(prediction.score, 2),
            "delta": round(prediction.score - baseline.score, 2),
            "confidence": round(prediction.confidence, 2),
        })
    results.sort(key=lambda item: item["delta"], reverse=True)
    return {
        "baseline_score": round(baseline.score, 2),
        "variants": results,
        "best": results[0] if results and results[0]["delta"] > 0 else None,
    }
