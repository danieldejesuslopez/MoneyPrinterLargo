"""Walk-forward backtest: what would Brain have picked, and was it better?

Training metrics answer "does the model fit". This answers the only question
that decides whether Brain is worth running: *if it had been choosing, would
the channel have done better?* (spec §73, §153, §155).

The simulation walks history forward. At each step it trains only on videos
published before the step, then ranks the videos of that step as if they had
been a candidate set, and records what its top pick actually went on to do
versus what the batch averaged. Nothing in the loop can see the future,
which is the entire point — a backtest that trains once on everything and
scores the past is a way of proving that a model has memorized it.

The honest caveats, stated because the plan insists on them (spec §82):

  - The "candidate set" is the set of videos that were *actually made*.
    Brain never gets to be judged on the ideas nobody produced, so this
    measures ranking skill within a real batch, not creative upside.
  - Lift is correlational. A better pick in a good week is not proof that
    the pick caused the week.
"""

from __future__ import annotations

from typing import Any, Sequence

from ..config import get_reward_weights
from ..constants import MIN_SAMPLES_PER_TARGET, TARGET_NORMALIZED_VIEWS
from ..data import dataset as ds
from . import evaluate as ev
from .train import train

#: Rows required before the first simulated decision.
DEFAULT_WARMUP = 40
#: Videos per simulated candidate set.
DEFAULT_BATCH = 5


def _batch_rewards(
    rows: Sequence[dict[str, Any]], weights: dict[str, float]
) -> list[tuple[dict[str, Any], float]]:
    scored = []
    for row in rows:
        reward = ev.row_reward(row, weights)
        if reward is not None:
            scored.append((row, reward))
    return scored


def walk_forward(
    rows: Sequence[dict[str, Any]] | None = None,
    warmup: int = DEFAULT_WARMUP,
    batch_size: int = DEFAULT_BATCH,
    account_id: str = "",
) -> dict[str, Any]:
    """Simulate Brain choosing, one batch at a time, through history."""
    dataset_rows = [
        row for row in (rows if rows is not None else ds.load_dataset())
        if row.get("mature")
    ]
    dataset_rows.sort(key=lambda row: str(row.get("published_at") or ""))
    weights = get_reward_weights(account_id)

    if len(dataset_rows) < warmup + batch_size:
        return {
            "ran": False,
            "reason": (
                f"{len(dataset_rows)} mature rows; a backtest needs at least "
                f"{warmup + batch_size} (warm-up plus one batch)."
            ),
            "rows": len(dataset_rows),
        }

    steps: list[dict[str, Any]] = []
    position = warmup
    while position + batch_size <= len(dataset_rows):
        history = dataset_rows[:position]
        batch = dataset_rows[position:position + batch_size]
        position += batch_size

        model, _ = train(history, account_id=account_id)
        if model is None:
            continue

        scored = _batch_rewards(batch, weights)
        if len(scored) < 2:
            continue

        predictions = [
            (row, ev.predicted_reward(model, row, weights)) for row, _ in scored
        ]
        if any(value is None for _, value in predictions):
            continue

        actual = [reward for _, reward in scored]
        predicted = [value for _, value in predictions]
        best_index = max(range(len(predicted)), key=lambda i: predicted[i])

        chosen_reward = actual[best_index]
        average_reward = sum(actual) / len(actual)
        best_possible = max(actual)

        steps.append({
            "at": batch[0].get("published_at"),
            "batch": len(actual),
            "chosen": scored[best_index][0].get("title"),
            "chosen_reward": round(chosen_reward, 4),
            "batch_mean_reward": round(average_reward, 4),
            "best_possible_reward": round(best_possible, 4),
            "lift_vs_mean": round(chosen_reward - average_reward, 4),
            "regret": round(best_possible - chosen_reward, 4),
            "picked_the_best": chosen_reward >= best_possible - 1e-9,
            "spearman": ev.spearman(actual, predicted),
        })

    return summarize_backtest(steps, len(dataset_rows))


def summarize_backtest(steps: list[dict[str, Any]], row_count: int) -> dict[str, Any]:
    """Aggregate the walk-forward steps into a verdict."""
    if not steps:
        return {
            "ran": False,
            "reason": "no step produced a trainable model",
            "rows": row_count,
            "steps": [],
        }

    lifts = [step["lift_vs_mean"] for step in steps]
    regrets = [step["regret"] for step in steps]
    correlations = [
        step["spearman"] for step in steps if step["spearman"] is not None
    ]
    top1 = sum(1 for step in steps if step["picked_the_best"])

    mean_lift = sum(lifts) / len(lifts)
    return {
        "ran": True,
        "rows": row_count,
        "steps": steps,
        "step_count": len(steps),
        "mean_lift_vs_mean": round(mean_lift, 4),
        "mean_regret": round(sum(regrets) / len(regrets), 4),
        "top1_accuracy": round(top1 / len(steps), 4),
        "mean_spearman": (
            round(sum(correlations) / len(correlations), 4) if correlations else None
        ),
        "verdict": _verdict(mean_lift, top1 / len(steps), len(steps)),
    }


def _verdict(mean_lift: float, top1_accuracy: float, step_count: int) -> str:
    """A sentence a person can act on, with the sample size attached."""
    if step_count < 5:
        return (
            f"Only {step_count} simulated decisions - not enough to conclude "
            "anything. Treat any number here as provisional."
        )
    if mean_lift <= 0:
        return (
            f"Over {step_count} batches Brain's pick averaged {mean_lift:+.3f} "
            "reward against the batch mean. It is not choosing better than "
            "chance yet - keep it in shadow mode."
        )
    return (
        f"Over {step_count} batches Brain's pick averaged {mean_lift:+.3f} reward "
        f"above the batch mean and picked the single best video "
        f"{top1_accuracy:.0%} of the time."
    )


# -- Predictive backtest -------------------------------------------------

#: Own videos required before the first prediction is scored. Matched to
#: `MIN_TRAINING_ROWS` so no step is silently skipped for having too little
#: history to train on at all.
PREDICTIVE_WARMUP = 40


def walk_forward_predictions(
    rows=None,
    target: str = TARGET_NORMALIZED_VIEWS,
    warmup: int = PREDICTIVE_WARMUP,
    account_id: str = "",
    stride: int = 1,
) -> dict:
    """The shared engine: one prediction per video, trained only on its past.

    Returns the raw per-video arrays rather than a summary, because two
    consumers slice them differently: `predictive_backtest` pools all of them,
    and the research loop splits them into a search era and a later
    confirmation era it is not allowed to tune on.

    `stride` scores every k-th video. Only tests should use it: skipping
    videos makes the estimate noisier, never better.
    """
    dataset_rows = [
        row for row in (rows if rows is not None else ds.load_dataset())
        if row.get("mature") and not row.get("is_reference")
    ]
    dataset_rows.sort(key=lambda row: str(row.get("published_at") or ""))

    out = {
        "rows": len(dataset_rows), "actual": [], "predicted": [], "null": [],
        "format_null": [], "kinds": [], "video_ids": [], "published_at": [],
    }
    if len(dataset_rows) < warmup + 1:
        return out

    for position in range(warmup, len(dataset_rows), max(1, stride)):
        history = dataset_rows[:position]
        current = dataset_rows[position]
        features, next_label, _ = ds.to_matrix([current], target)
        if not next_label:
            continue

        model, _ = train(history, account_id=account_id, targets=(target,))
        head = model.heads.get(target) if model else None
        _, history_labels, history_rows = ds.to_matrix(history, target)
        if head is None or not history_labels:
            continue

        # A second, harder null: the mean of the *same format*. Shorts and
        # long-form behave nothing alike on a small channel, and format is
        # known before publishing - so a null model that ignores it is
        # easier to beat than it should be. Reporting only the global mean
        # would let the format effect masquerade as predictive skill.
        same_format = [
            label for label, row in zip(history_labels, history_rows)
            if row.get("kind") == current.get("kind")
        ]
        overall = sum(history_labels) / len(history_labels)

        out["actual"].append(next_label[0])
        out["predicted"].append(head.predict(features[0]))
        out["null"].append(overall)
        out["format_null"].append(
            sum(same_format) / len(same_format) if same_format else overall
        )
        out["kinds"].append(current.get("kind"))
        out["video_ids"].append(current.get("video_id"))
        out["published_at"].append(current.get("published_at"))
    return out


def predictive_backtest(
    rows=None,
    target: str = TARGET_NORMALIZED_VIEWS,
    warmup: int = PREDICTIVE_WARMUP,
    account_id: str = "",
    bootstrap: int = 2000,
) -> dict:
    """How much closer than the null model, video by video, through history.

    One video at a time: fit on everything published before it, predict it,
    keep the residual. The null model is the historical mean carried forward
    - never the mean of the videos being predicted, which is a number no
    forecaster can see.

    Residuals are pooled into a single RMSE rather than averaged per batch.
    Averaging per-batch RMSEs weights a three-video batch like a thirty-video
    one and lets one outlier batch decide the headline; pooling answers the
    question actually asked - across these N videos, how much closer was the
    model than carrying the mean forward?

    Reference-channel rows train but are never scored: the question is
    whether Brain predicts *your* channel.
    """
    walk = walk_forward_predictions(rows, target, warmup, account_id)
    if walk["rows"] < warmup + 5:
        return {
            "ran": False,
            "reason": (
                f"{walk['rows']} of your own mature videos; this needs at "
                f"least {warmup + 5}."
            ),
            "rows": walk["rows"],
        }

    actual = walk["actual"]
    predicted = walk["predicted"]
    baseline = walk["null"]
    format_baseline = walk["format_null"]
    dataset_rows = [None] * walk["rows"]

    if len(actual) < 5:
        return {"ran": False, "reason": "too few scored predictions",
                "rows": len(dataset_rows), "scored": len(actual)}

    model_metrics = ev.regression_metrics(actual, predicted)
    null_metrics = ev.regression_metrics(actual, baseline)
    format_metrics = ev.regression_metrics(actual, format_baseline)
    format_gain = (
        100.0 * (format_metrics["rmse"] - model_metrics["rmse"]) / format_metrics["rmse"]
        if format_metrics["rmse"] else 0.0
    )
    format_low, format_high = _bootstrap_gain(
        actual, predicted, format_baseline, bootstrap
    )
    gain = (
        100.0 * (null_metrics["rmse"] - model_metrics["rmse"]) / null_metrics["rmse"]
        if null_metrics["rmse"] else 0.0
    )
    low, high = _bootstrap_gain(actual, predicted, baseline, bootstrap)

    return {
        "ran": True,
        "target": target,
        "rows": len(dataset_rows),
        "scored": len(actual),
        "model_rmse": round(model_metrics["rmse"], 5),
        "null_rmse": round(null_metrics["rmse"], 5),
        "rmse_gain_pct": round(gain, 2),
        "mae_gain_pct": round(
            100.0 * (null_metrics["mae"] - model_metrics["mae"]) / null_metrics["mae"]
            if null_metrics["mae"] else 0.0, 2),
        "ci95": [round(low, 2), round(high, 2)],
        "format_null_rmse": round(format_metrics["rmse"], 5),
        "format_rmse_gain_pct": round(format_gain, 2),
        "format_ci95": [round(format_low, 2), round(format_high, 2)],
        "closer_than_null": sum(
            1 for a, m, b in zip(actual, predicted, baseline)
            if abs(m - a) < abs(b - a)
        ),
        "spearman": ev.spearman(actual, predicted),
        "verdict": _predictive_verdict(gain, low, len(actual)),
        "format_verdict": _format_verdict(format_gain, format_low),
    }


def _bootstrap_gain(actual, predicted, baseline, draws: int):
    """95% interval on the gain, resampling the scored videos."""
    import math
    import random

    if draws <= 0 or len(actual) < 5:
        return 0.0, 0.0
    rng = random.Random(20260909)
    count = len(actual)
    gains = []
    for _ in range(draws):
        picks = [rng.randrange(count) for _ in range(count)]
        model_sq = sum((actual[i] - predicted[i]) ** 2 for i in picks) / count
        null_sq = sum((actual[i] - baseline[i]) ** 2 for i in picks) / count
        null_rmse = math.sqrt(null_sq)
        if null_rmse > 0:
            gains.append(100.0 * (null_rmse - math.sqrt(model_sq)) / null_rmse)
    if not gains:
        return 0.0, 0.0
    gains.sort()
    return gains[int(0.025 * len(gains))], gains[int(0.975 * len(gains))]


def _format_verdict(gain: float, low: float) -> str:
    if gain <= 0:
        return (
            f"Against the same-format mean the model is {gain:+.1f}% - no better. "
            "Most of the gain above is the Shorts-vs-long-form gap, which a "
            "format-aware null already knows. Predicting which Short beats "
            "which other Short is still unsolved."
        )
    if low <= 0:
        return (
            f"Against the same-format mean: {gain:+.1f}%, but the interval still "
            "includes zero."
        )
    return f"Against the same-format mean: {gain:+.1f}% (interval from {low:+.1f}%)."


def _predictive_verdict(gain: float, low: float, scored: int) -> str:
    if low <= 0:
        return (
            f"{gain:+.1f}% closer than the null model over {scored} videos, but the "
            "95% interval still includes zero - not yet distinguishable from luck."
        )
    return (
        f"{gain:+.1f}% closer than the null model over {scored} videos, and the "
        f"whole 95% interval is above zero (from {low:+.1f}%)."
    )


def head_readiness(rows: Sequence[dict[str, Any]] | None = None) -> dict[str, Any]:
    """How close each target is to having enough data to be learnable."""
    dataset_rows = list(rows if rows is not None else ds.load_dataset())
    mature = [row for row in dataset_rows if row.get("mature")]
    readiness: dict[str, Any] = {}
    for target in ev.TARGETS:
        _, labels, _ = ds.to_matrix(mature, target)
        readiness[target] = {
            "labeled_rows": len(labels),
            "needed": MIN_SAMPLES_PER_TARGET,
            "ready": len(labels) >= MIN_SAMPLES_PER_TARGET,
        }
    return readiness
