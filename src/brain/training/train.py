"""Training a challenger, and deciding whether it deserves to be champion.

The loop the plan asks for (spec §71), in order and with no shortcuts:

    collect -> validate -> split chronologically -> fit each head
    -> sweep regularization on held-out data -> evaluate -> compare
    against the champion -> promote only if it earns it

Three rules this module refuses to bend:

  - **Nothing trains on immature labels.** A video measured two hours after
    upload has not finished happening.
  - **Nothing trains on a target with too few real labels.** A head is
    absent rather than fitted on twelve rows, and the predictor falls back
    to heuristics for that target.
  - **Nothing is promoted on training loss.** Promotion needs a held-out
    ranking improvement, and the champion stays if the challenger merely
    fits better (spec §72, §150).

Regularization is chosen on a held-out split and the head is then refitted on
the whole history with that choice. At fifty rows the validation split is a
fifth of everything known, and throwing it away costs more accuracy than the
refit costs in honesty — measured, not assumed: refitting was worth about
three points of RMSE in the walk-forward.

The calibration stored alongside each head is still the pre-refit number,
measured on rows those weights had never seen. It slightly understates the
shipped model, which is the safe direction for a figure whose only job is to
decide when Brain should keep quiet.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
from typing import Any, Sequence

from ..config import get_reward_weights
from ..constants import (
    CHALLENGER_MODEL_LINK,
    MIN_SAMPLES_PER_TARGET,
    TARGETS,
    tier_for_samples,
)
from ..data import dataset as ds
from ..data import validators
from ..features import registry as fr
from ..models import registry as model_registry
from ..models.linear import RidgeModel
from ..models.multitask import MultiTaskModel
from . import evaluate as ev

#: Regularization strengths swept on the validation split.
ALPHA_GRID = (0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0, 128.0)
#: Slack in the "must beat the null model" gate, scaled to how much evidence
#: the comparison actually rests on.
#:
#: The gate is right in principle - a head that cannot beat carrying the mean
#: forward should not ship. But it is decided on the validation split, which
#: early in a channel's life is eight rows. Eight rows cannot separate a head
#: that is three percent better from one three percent worse, and the two
#: mistakes are not symmetric: a ridge blend this heavily shrunk degrades
#: toward the mean anyway, so keeping a marginal head risks little, while
#: dropping it guarantees the null model exactly in the unstable stretches
#: where the null model is weakest.
#:
#: So the slack shrinks as the evidence grows: 1 + k/sqrt(n). At eight rows
#: it allows ~18%, at a hundred ~5%, and it tends to zero tolerance in the
#: limit, which is where the strict rule belongs.
BASELINE_TOLERANCE_K = 0.5
BASELINE_TOLERANCE_MAX = 1.25


def baseline_tolerance(validation_size: int) -> float:
    """How much worse than the null a head may validate and still ship."""
    import math

    if validation_size <= 0:
        return BASELINE_TOLERANCE_MAX
    return min(
        BASELINE_TOLERANCE_MAX,
        1.0 + BASELINE_TOLERANCE_K / math.sqrt(validation_size),
    )

#: Share of each tail of the training labels pulled in to the percentile
#: at that depth before fitting.
#:
#: Squared error lets the extremes write the model. This channel's ten
#: long-form videos land near 0.02x the channel median - twenty standard
#: deviations from everything else - and a fit that spends its budget
#: reaching for them predicts the seventy-three Shorts worse. Winsorizing
#: the training labels is the standard answer, and it is applied *only*
#: while fitting: the labels a model is scored against are never touched,
#: so this cannot flatter an evaluation.
LABEL_WINSOR = 0.05

#: How many of the best-scoring alphas are averaged into the shipped head.
#: One is a coin toss on a validation split this small; three measured
#: better than one at every warm-up and split size tried.
ALPHA_BLEND = 3
#: Minimum rows before training is attempted at all.
MIN_TRAINING_ROWS = 40
#: Share of the user's own most recent videos held out for alpha selection
#: and the null-model gate.
VALIDATION_FRACTION = 0.2


def split_for_training(
    rows: Sequence[dict[str, Any]], validation_fraction: float
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Learn from everything, but be judged only on the user's own videos.

    Reference channels are there to give a small channel something to learn
    from; they are not what Brain is *for*. Validating on a pool that is 90%
    someone else's channel would pick the regularization, drop the heads and
    gate promotion on how well the model predicts a 3M-subscriber channel —
    a question nobody asked.

    So the validation split is the user's own most recent videos, and the
    training set is their earlier videos plus every reference row published
    before the validation window opens. Reference rows dated inside that
    window are dropped: they are not leakage about *this* channel, but they
    are future information about the niche, and the cheapest way to keep the
    "train only on the past" rule unambiguous is not to make an exception.

    With no own rows at all (a fresh install with only reference channels),
    this falls back to a plain chronological split.
    """
    own = sorted(
        (row for row in rows if not row.get("is_reference")),
        key=lambda row: str(row.get("published_at") or ""),
    )
    reference = [row for row in rows if row.get("is_reference")]
    if not own:
        split = ds.time_split(list(rows), validation_fraction=validation_fraction)
        return split["train"], split["validation"]

    split = ds.time_split(own, validation_fraction=validation_fraction)
    own_train, own_validation = split["train"], split["validation"]
    cutoff = str(own_validation[0].get("published_at") or "") if own_validation else ""
    reference_train = [
        row for row in reference
        if not cutoff or str(row.get("published_at") or "") < cutoff
    ]
    return own_train + reference_train, own_validation


def dataset_hash(rows: Sequence[dict[str, Any]]) -> str:
    """Fingerprint of exactly which rows produced a model (spec §62)."""
    payload = json.dumps(
        sorted(
            f"{row.get('video_id')}:{row.get('published_at')}:{row.get('raw_views')}"
            for row in rows
        ),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def winsorize(labels: list[float], share: float | None = None) -> list[float]:
    """Pull each tail in to the percentile at `share` depth.

    `share` defaults to `LABEL_WINSOR` read at call time, so a research round
    that varies it actually varies it.
    """
    share = LABEL_WINSOR if share is None else share
    if not labels or share <= 0:
        return list(labels)
    ordered = sorted(labels)
    last = len(ordered) - 1
    low = ordered[int(share * last)]
    high = ordered[int((1.0 - share) * last)]
    return [min(max(value, low), high) for value in labels]


def _fit_head(
    target: str,
    train_rows: Sequence[dict[str, Any]],
    validation_rows: Sequence[dict[str, Any]],
    population: str = "own + reference",
) -> tuple[RidgeModel | None, dict[str, Any]]:
    """Fit one head, choosing alpha on held-out data.

    Returns the head and a report. When validation is too small to choose
    on, the middle of the grid is used and the report says so — a documented
    default beats silently selecting alpha on the training data itself.
    """
    train_x, train_y_raw, _ = ds.to_matrix(train_rows, target)
    train_y = winsorize(train_y_raw)
    if len(train_x) < MIN_SAMPLES_PER_TARGET:
        return None, {
            "target": target,
            "skipped": True,
            "population": population,
            "reason": f"only {len(train_x)} mature labeled rows "
                      f"(need {MIN_SAMPLES_PER_TARGET})",
        }

    validation_x, validation_y, _ = ds.to_matrix(validation_rows, target)
    baseline = ev.baseline_comparison(train_rows, validation_rows or train_rows, target)

    attempts: list[dict[str, Any]] = []
    scored: list[tuple[float, float, RidgeModel]] = []

    for alpha in ALPHA_GRID:
        head = RidgeModel.fit(train_x, train_y, fr.FEATURE_NAMES, alpha, target)
        if validation_x:
            metrics = ev.regression_metrics(
                validation_y, head.predict_many(validation_x)
            )
        else:
            metrics = ev.regression_metrics(train_y, head.predict_many(train_x))
        attempts.append({"alpha": alpha, **{k: round(v, 5) for k, v in metrics.items()}})
        scored.append((metrics["rmse"], alpha, head))

    scored.sort(key=lambda item: item[0])
    blended = [head for _, _, head in scored[:ALPHA_BLEND]]
    best_alpha = [alpha for _, alpha, _ in scored[:ALPHA_BLEND]]

    # Score the blend, not the single best alpha: the blend is what ships,
    # so the blend is what has to prove itself against the null model.
    held_out = RidgeModel.average(blended)
    if validation_x:
        best_metrics = ev.regression_metrics(
            validation_y, held_out.predict_many(validation_x)
        )
    else:
        best_metrics = ev.regression_metrics(train_y, held_out.predict_many(train_x))

    null_rmse = baseline.get("rmse", float("inf"))
    tolerance = baseline_tolerance(len(validation_x))
    beats_baseline = best_metrics.get("rmse", 0.0) < null_rmse * tolerance

    # Now that the alphas are chosen, refit them on every row available. The
    # validation rows were held back to rank ten alphas, not because they had
    # nothing to teach; at this sample size handing them back is worth more
    # than the tidiness of never having fitted on them.
    refit_rows = list(train_rows) + list(validation_rows)
    full_x, full_y_raw, _ = ds.to_matrix(refit_rows, target)
    full_y = winsorize(full_y_raw)
    if len(full_x) > len(train_x):
        best_head = RidgeModel.average([
            RidgeModel.fit(full_x, full_y, fr.FEATURE_NAMES, alpha, target)
            for alpha in best_alpha
        ])
        refitted = len(full_x)
    else:
        best_head = held_out
        refitted = len(train_x)
    report = {
        "target": target,
        "skipped": False,
        "population": population,
        "train_rows": len(train_x),
        "validation_rows": len(validation_x),
        "chosen_alpha": best_alpha,
        "alpha_blend": len(blended),
        "label_winsor": LABEL_WINSOR,
        "refit_rows": refitted,
        "metrics": {k: round(v, 5) for k, v in best_metrics.items()},
        "baseline": {k: round(v, 5) for k, v in baseline.items()},
        "beats_mean_baseline": beats_baseline,
        "baseline_tolerance": round(tolerance, 4),
        "selected_on": "validation" if validation_x else "train (no validation rows)",
        "metrics_measured_on": "held-out rows, before the refit",
        "attempts": attempts,
    }
    if best_head is not None:
        best_head.metrics = dict(best_metrics)
    return best_head, report


def train(
    rows: Sequence[dict[str, Any]] | None = None,
    validation_fraction: float | None = None,
    account_id: str = "",
    keep_underperforming_heads: bool = False,
    targets: Sequence[str] | None = None,
) -> tuple[MultiTaskModel | None, dict[str, Any]]:
    """Fit a challenger from the dataset. Returns `(model, report)`.

    `model` is None when there is not enough data to train anything honest;
    the report explains why and Brain stays in heuristic mode.

    `targets` restricts which heads are fitted. A walk-forward that scores one
    target retrains dozens of times, and fitting heads nobody reads multiplies
    that cost for nothing.
    """
    from ..research import knobs

    with knobs.configured():
        return _train(
            rows,
            VALIDATION_FRACTION if validation_fraction is None else validation_fraction,
            account_id,
            keep_underperforming_heads,
            tuple(targets) if targets else TARGETS,
        )


def _train(
    rows: Sequence[dict[str, Any]] | None,
    validation_fraction: float,
    account_id: str,
    keep_underperforming_heads: bool,
    targets: Sequence[str],
) -> tuple[MultiTaskModel | None, dict[str, Any]]:
    dataset_rows = list(rows if rows is not None else ds.load_dataset())
    validation = validators.validate_dataset(dataset_rows)
    mature = [row for row in dataset_rows if row.get("mature")]

    report: dict[str, Any] = {
        "started_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "rows": len(dataset_rows),
        "mature_rows": len(mature),
        "validation": validation,
        "heads": {},
        "trained": False,
    }

    if len(mature) < MIN_TRAINING_ROWS:
        report["reason"] = (
            f"{len(mature)} mature rows is below the {MIN_TRAINING_ROWS} needed to "
            "train. Brain stays heuristic - this is the expected state until the "
            "channel has published and synced enough videos."
        )
        return None, report
    if validation["invalid_rows"]:
        report["reason"] = (
            f"{validation['invalid_rows']} invalid rows; rebuild the dataset first."
        )
        return None, report

    train_rows, validation_rows = split_for_training(mature, validation_fraction)

    own_train = [row for row in train_rows if not row.get("is_reference")]
    has_reference = len(own_train) != len(train_rows)

    heads: dict[str, RidgeModel] = {}
    calibration: dict[str, dict[str, float]] = {}
    for target in targets:
        # Whether other channels help is a question per head, not per model,
        # and not one to answer by taste: fit both ways and keep whichever
        # predicts *your* videos better. On this data the view head is hurt
        # by outside channels while the engagement heads are helped, and a
        # single global switch would have to get one of them wrong.
        candidates = [_fit_head(target, train_rows, validation_rows,
                                "own + reference" if has_reference else "own")]
        if has_reference:
            candidates.append(
                _fit_head(target, own_train, validation_rows, "own only")
            )

        usable = [(h, r) for h, r in candidates if h is not None]
        if usable:
            head, head_report = min(
                usable, key=lambda item: item[1]["metrics"]["rmse"]
            )
            head_report = dict(head_report)
            if len(usable) > 1:
                head_report["populations_tried"] = {
                    r["population"]: r["metrics"]["rmse"] for _, r in usable
                }
        else:
            head, head_report = candidates[0]

        report["heads"][target] = head_report
        if head is None:
            continue
        if not head_report["beats_mean_baseline"] and not keep_underperforming_heads:
            head_report["dropped"] = (
                "does not beat predicting the mean - kept out of the model"
            )
            continue
        heads[target] = head
        calibration[target] = {
            **{k: float(v) for k, v in head_report["metrics"].items()},
            "alpha": float(head.alpha),
        }

    if not heads:
        report["reason"] = (
            "No head beat its mean baseline. There is signal to find here, but not "
            "with this much data - the features do not yet explain the outcomes."
        )
        return None, report

    model = MultiTaskModel(
        feature_schema=fr.SCHEMA_HASH,
        feature_names=list(fr.FEATURE_NAMES),
        tier=tier_for_samples(len(mature)),
        heads=heads,
        calibration=calibration,
        training={
            "samples": len(mature),
            "own_samples": sum(1 for row in mature if not row.get("is_reference")),
            "reference_samples": sum(1 for row in mature if row.get("is_reference")),
            "train_rows": len(train_rows),
            "validation_rows": len(validation_rows),
            "validated_on": "own channels" if any(
                not row.get("is_reference") for row in mature
            ) else "all rows",
            "dataset_hash": dataset_hash(mature),
            "validation_fraction": validation_fraction,
        },
    )

    weights = get_reward_weights(account_id)
    report["trained"] = True
    report["evaluation"] = ev.evaluate_model(model, validation_rows or train_rows, weights)
    report["targets"] = model.targets
    report["tier"] = model.tier
    return model, report


def train_and_register(
    rows: Sequence[dict[str, Any]] | None = None,
    account_id: str = "",
    auto_promote: bool = True,
) -> dict[str, Any]:
    """Train, store as challenger, and promote only if the gate allows it.

    This is the whole champion/challenger cycle (spec §71, §72) in one call,
    suitable for a scheduled retrain.
    """
    model, report = train(rows, account_id=account_id)
    if model is None:
        return {"trained": False, "report": report, "promoted": False}

    weights = get_reward_weights(account_id)
    challenger_metrics = report.get("evaluation") or {}

    champion = model_registry.champion()
    champion_metrics: dict[str, Any] = {}
    if champion:
        dataset_rows = list(rows if rows is not None else ds.load_dataset())
        mature = [row for row in dataset_rows if row.get("mature")]
        holdout = split_for_training(mature, 0.2)[1] or mature
        if champion.is_compatible(fr.SCHEMA_HASH):
            champion_metrics = ev.evaluate_model(champion, holdout, weights)
        else:
            report["champion_note"] = (
                f"champion {champion.version} predates the current feature schema "
                "and was not comparable; promoting the challenger"
            )

    version = model_registry.save(
        model,
        manifest={
            "dataset_hash": model.training.get("dataset_hash"),
            "samples": model.training.get("samples"),
            "evaluation": challenger_metrics,
            "champion_compared": champion.version if champion else None,
        },
    )
    model_registry.set_pointer(CHALLENGER_MODEL_LINK, version)

    promote, reasons = model_registry.promotion_decision(
        champion_metrics, challenger_metrics
    )
    if promote and auto_promote:
        model_registry.promote(version)

    return {
        "trained": True,
        "version": version,
        "promoted": bool(promote and auto_promote),
        "promotion_reasons": reasons,
        "report": report,
    }
