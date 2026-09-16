"""Scoring a proposed change the only way that cannot flatter it.

Every experiment is run through the real training code — the same
`walk_forward_predictions` engine `brain backtest --predictive` uses — with its
knob values applied. Then the scored videos are split in time:

  - the **search era** (the older two thirds) decides whether an idea looks
    promising at all;
  - the **confirmation era** (the most recent third) decides whether it
    survives on videos the search step never got to look at.

Three details make the comparison fair rather than merely plausible:

  - **Relative gain, not raw RMSE.** A knob like `peer_window` changes the
    label itself, so two configurations may be predicting different numbers.
    Gain over each configuration's *own* null model is scale-free and can be
    compared across label definitions.
  - **Aligned videos.** Candidate and current are compared on exactly the
    videos both managed to score, matched by id.
  - **Paired bootstrap.** Confirmation resamples the same videos for both
    sides, so the question is "on these videos, does the change help?" and
    not "are two noisy numbers far apart?".
"""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..constants import TARGET_NORMALIZED_VIEWS
from ..data import collector
from ..data import dataset as ds
from ..training import evaluate as ev
from ..training.backtest import PREDICTIVE_WARMUP, walk_forward_predictions
from . import knobs

#: Share of scored videos (oldest first) the search step may look at.
SEARCH_SHARE = 2.0 / 3.0
#: A candidate must beat the current setup by at least this many percentage
#: points on the search era to be worth confirming. Smaller differences are
#: indistinguishable from reshuffling noise at this sample size.
MIN_SEARCH_GAIN = 1.0
#: Probability, under paired bootstrap, that the candidate is better on the
#: confirmation era. 0.8 is lenient by design - the era is small - and is
#: backed by requiring the search era to agree first.
MIN_CONFIRM_PROBABILITY = 0.8
BOOTSTRAP_DRAWS = 2000

OBJECTIVES = ("format", "global")


@dataclass
class Evaluation:
    """One configuration's walk-forward, plus what it was measured under."""

    changes: dict[str, Any]
    video_ids: list[str] = field(default_factory=list)
    actual: list[float] = field(default_factory=list)
    predicted: list[float] = field(default_factory=list)
    null: list[float] = field(default_factory=list)
    format_null: list[float] = field(default_factory=list)
    kinds: list[str] = field(default_factory=list)

    @property
    def scored(self) -> int:
        return len(self.actual)

    def index(self) -> dict[str, int]:
        return {video_id: position for position, video_id in enumerate(self.video_ids)}


def own_rows() -> list[dict[str, Any]]:
    """Fresh rows for the user's own channels, under whatever knobs are active."""
    channels = [c for c in collector.load_channels() if not c.is_reference]
    return ds.build_rows(channels)


def evaluate(
    changes: dict[str, Any],
    base_rows: Sequence[dict[str, Any]] | None = None,
    target: str = TARGET_NORMALIZED_VIEWS,
    warmup: int = PREDICTIVE_WARMUP,
    stride: int = 1,
) -> Evaluation:
    """Run the walk-forward with approved overrides plus `changes` applied.

    Approved overrides are merged in explicitly so the experiment's values win
    and nothing inside training can quietly re-apply the config over them.
    Rows are rebuilt only when a label-changing knob is part of the set.
    """
    approved, _ = knobs.approved_overrides()
    merged = {**approved, **(changes or {})}

    with knobs.applied(merged):
        if base_rows is None or knobs.needs_rebuild(changes or {}):
            rows = own_rows()
        else:
            rows = list(base_rows)
        walk = walk_forward_predictions(rows, target, warmup, stride=stride)

    return Evaluation(
        changes=dict(changes or {}),
        video_ids=[str(v) for v in walk["video_ids"]],
        actual=list(walk["actual"]),
        predicted=list(walk["predicted"]),
        null=list(walk["null"]),
        format_null=list(walk["format_null"]),
        kinds=[str(k) for k in walk["kinds"]],
    )


# -- metrics --------------------------------------------------------------


def _rmse(actual: Sequence[float], predicted: Sequence[float]) -> float:
    if not actual:
        return 0.0
    return math.sqrt(sum((a - p) ** 2 for a, p in zip(actual, predicted)) / len(actual))


def gain(actual, predicted, null) -> float | None:
    """Percentage by which the model's RMSE undercuts the null model's."""
    null_rmse = _rmse(actual, null)
    if not actual or null_rmse <= 0:
        return None
    return 100.0 * (null_rmse - _rmse(actual, predicted)) / null_rmse


def summarize(evaluation: Evaluation, positions: Sequence[int] | None = None) -> dict[str, Any]:
    picks = list(range(evaluation.scored)) if positions is None else list(positions)
    actual = [evaluation.actual[i] for i in picks]
    predicted = [evaluation.predicted[i] for i in picks]
    null = [evaluation.null[i] for i in picks]
    format_null = [evaluation.format_null[i] for i in picks]
    return {
        "n": len(picks),
        "model_rmse": round(_rmse(actual, predicted), 5),
        "global_gain_pct": _round(gain(actual, predicted, null)),
        "format_gain_pct": _round(gain(actual, predicted, format_null)),
        "spearman": _round(ev.spearman(actual, predicted), 4),
    }


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def _objective_null(evaluation: Evaluation, objective: str) -> list[float]:
    return evaluation.format_null if objective == "format" else evaluation.null


# -- comparison -------------------------------------------------------------


def aligned_eras(
    current: Evaluation, candidate: Evaluation
) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """Pairs of (current_index, candidate_index) for the search and confirm eras.

    Only videos both configurations scored are compared. The split point is
    taken over the aligned, chronologically ordered set, so both sides see
    exactly the same videos in each era.
    """
    candidate_index = candidate.index()
    pairs = [
        (position, candidate_index[video_id])
        for position, video_id in enumerate(current.video_ids)
        if video_id in candidate_index
    ]
    cut = int(round(len(pairs) * SEARCH_SHARE))
    return pairs[:cut], pairs[cut:]


def era_signature(pairs: Sequence[tuple[int, int]], evaluation: Evaluation) -> str:
    """Stable id of a confirmation era, so reuse of it can be counted."""
    ids = "|".join(evaluation.video_ids[i] for i, _ in pairs)
    return hashlib.sha256(ids.encode("utf-8")).hexdigest()[:16]


def _era_gain(evaluation: Evaluation, indices: Sequence[int], objective: str) -> float | None:
    null = _objective_null(evaluation, objective)
    return gain(
        [evaluation.actual[i] for i in indices],
        [evaluation.predicted[i] for i in indices],
        [null[i] for i in indices],
    )


def paired_probability_better(
    current: Evaluation,
    candidate: Evaluation,
    pairs: Sequence[tuple[int, int]],
    objective: str,
    draws: int = BOOTSTRAP_DRAWS,
    seed: int = 20260912,
) -> float | None:
    """P(candidate's gain > current's gain), resampling the same videos."""
    if len(pairs) < 3:
        return None
    rng = random.Random(seed)
    wins = 0
    valid = 0
    for _ in range(draws):
        sample = [pairs[rng.randrange(len(pairs))] for _ in range(len(pairs))]
        current_gain = _era_gain(current, [c for c, _ in sample], objective)
        candidate_gain = _era_gain(candidate, [k for _, k in sample], objective)
        if current_gain is None or candidate_gain is None:
            continue
        valid += 1
        if candidate_gain > current_gain:
            wins += 1
    return wins / valid if valid else None


def compare(
    current: Evaluation,
    candidate: Evaluation,
    objective: str = "format",
) -> dict[str, Any]:
    """Verdict on one candidate against the current configuration."""
    search, confirm = aligned_eras(current, candidate)
    if len(search) < 5 or len(confirm) < 3:
        return {
            "verdict": "insufficient_data",
            "reason": (
                f"only {len(search)} search and {len(confirm)} confirmation videos "
                "were scored by both configurations"
            ),
        }

    search_current = _era_gain(current, [c for c, _ in search], objective)
    search_candidate = _era_gain(candidate, [k for _, k in search], objective)
    confirm_current = _era_gain(current, [c for c, _ in confirm], objective)
    confirm_candidate = _era_gain(candidate, [k for _, k in confirm], objective)

    search_delta = _delta(search_candidate, search_current)
    confirm_delta = _delta(confirm_candidate, confirm_current)
    probability = paired_probability_better(current, candidate, confirm, objective)

    if search_delta is None or search_delta < MIN_SEARCH_GAIN:
        verdict = "rejected_search"
    elif (
        confirm_delta is None or confirm_delta <= 0
        or probability is None or probability < MIN_CONFIRM_PROBABILITY
    ):
        verdict = "rejected_confirm"
    else:
        verdict = "recommended"

    return {
        "verdict": verdict,
        "objective": objective,
        "search": {
            "n": len(search), "current_gain_pct": _round(search_current),
            "candidate_gain_pct": _round(search_candidate), "delta_pp": _round(search_delta),
        },
        "confirm": {
            "n": len(confirm), "current_gain_pct": _round(confirm_current),
            "candidate_gain_pct": _round(confirm_candidate), "delta_pp": _round(confirm_delta),
            "probability_better": _round(probability, 3),
            "era": era_signature(confirm, current),
        },
    }


def _delta(candidate: float | None, current: float | None) -> float | None:
    if candidate is None or current is None:
        return None
    return candidate - current
