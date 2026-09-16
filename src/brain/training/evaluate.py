"""Metrics — regression accuracy, and the ranking quality that matters more.

Brain is never asked "how many views will this get". It is asked "which of
these fifty is the best one to make". Those are different problems: a model
with a mediocre RMSE that orders candidates correctly is useful, and a model
with a great RMSE that orders them wrongly is not (spec §74).

So every evaluation reports both, and the promotion gate reads the ranking
half. Rank correlation is computed with average ranks for ties, which is
what makes it well-behaved on the many-equal-scores case a small dataset
produces.
"""

from __future__ import annotations

import math
from typing import Any, Sequence

from ..constants import TARGETS
from ..data import dataset as ds
from ..data import normalizer as norm
from ..features import registry as fr
from ..models.multitask import MultiTaskModel


def regression_metrics(
    actual: Sequence[float], predicted: Sequence[float]
) -> dict[str, float]:
    """MAE, RMSE, R^2 and the residual spread used for confidence bands."""
    pairs = [(float(a), float(p)) for a, p in zip(actual, predicted)]
    if not pairs:
        return {"n": 0, "mae": 0.0, "rmse": 0.0, "r2": 0.0, "residual_std": 0.0}

    count = len(pairs)
    errors = [p - a for a, p in pairs]
    mae = sum(abs(e) for e in errors) / count
    rmse = math.sqrt(sum(e * e for e in errors) / count)

    mean_actual = sum(a for a, _ in pairs) / count
    total_variance = sum((a - mean_actual) ** 2 for a, _ in pairs)
    residual_variance = sum(e * e for e in errors)
    r2 = 1.0 - (residual_variance / total_variance) if total_variance > 1e-12 else 0.0

    mean_error = sum(errors) / count
    residual_std = math.sqrt(
        sum((e - mean_error) ** 2 for e in errors) / count
    )
    return {
        "n": count,
        "mae": mae,
        "rmse": rmse,
        "r2": r2,
        "residual_std": residual_std,
    }


def _ranks(values: Sequence[float]) -> list[float]:
    """Average ranks, so ties do not distort the correlation."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(order):
        stop = index
        while stop + 1 < len(order) and values[order[stop + 1]] == values[order[index]]:
            stop += 1
        average = (index + stop) / 2.0 + 1.0
        for position in range(index, stop + 1):
            ranks[order[position]] = average
        index = stop + 1
    return ranks


def spearman(actual: Sequence[float], predicted: Sequence[float]) -> float | None:
    """Rank correlation in [-1, 1], or None when it is undefined."""
    if len(actual) < 3 or len(actual) != len(predicted):
        return None
    actual_ranks = _ranks(actual)
    predicted_ranks = _ranks(predicted)
    count = len(actual_ranks)

    mean_a = sum(actual_ranks) / count
    mean_p = sum(predicted_ranks) / count
    covariance = sum(
        (a - mean_a) * (p - mean_p) for a, p in zip(actual_ranks, predicted_ranks)
    )
    variance_a = sum((a - mean_a) ** 2 for a in actual_ranks)
    variance_p = sum((p - mean_p) ** 2 for p in predicted_ranks)
    if variance_a <= 1e-12 or variance_p <= 1e-12:
        return None
    return covariance / math.sqrt(variance_a * variance_p)


def ndcg_at_k(actual: Sequence[float], predicted: Sequence[float], k: int = 5) -> float | None:
    """Normalized discounted cumulative gain of the model's top-k picks.

    "If Brain had chosen the k it liked best, how much of the achievable
    performance would it have captured?" — the closest single number to what
    the system is actually for.
    """
    if not actual or len(actual) != len(predicted):
        return None
    k = min(k, len(actual))
    gains = [max(0.0, value) for value in actual]

    by_prediction = sorted(range(len(gains)), key=lambda i: predicted[i], reverse=True)
    dcg = sum(
        gains[index] / math.log2(position + 2)
        for position, index in enumerate(by_prediction[:k])
    )
    ideal_order = sorted(gains, reverse=True)[:k]
    idcg = sum(
        gain / math.log2(position + 2) for position, gain in enumerate(ideal_order)
    )
    return dcg / idcg if idcg > 0 else None


def precision_at_k(
    actual: Sequence[float], predicted: Sequence[float], k: int = 3
) -> float | None:
    """Share of the model's top k that are genuinely in the true top k."""
    if len(actual) < k or len(actual) != len(predicted):
        return None
    truth = set(sorted(range(len(actual)), key=lambda i: actual[i], reverse=True)[:k])
    guess = sorted(range(len(predicted)), key=lambda i: predicted[i], reverse=True)[:k]
    return sum(1 for index in guess if index in truth) / k


def row_reward(row: dict[str, Any], weights: dict[str, float]) -> float | None:
    """The realized reward for a dataset row — the ranking ground truth."""
    return norm.reward(row.get("targets") or {}, weights)


def predicted_reward(
    model: MultiTaskModel, row: dict[str, Any], weights: dict[str, float]
) -> float | None:
    features = row.get("features") or {}
    values = [float(features.get(name, 0.0)) for name in fr.FEATURE_NAMES]
    absent = set(row.get("missing_features") or ())
    missing = {
        index for index, name in enumerate(fr.FEATURE_NAMES) if name in absent
    }
    targets = {
        target: ds.inverse_target(target, value)
        for target, value in model.predict(values, missing).items()
    }
    return norm.reward(targets, weights)


def ranking_metrics(
    model: MultiTaskModel, rows: Sequence[dict[str, Any]], weights: dict[str, float]
) -> dict[str, Any]:
    """How well the model orders held-out videos by realized reward."""
    actual: list[float] = []
    predicted: list[float] = []
    for row in rows:
        truth = row_reward(row, weights)
        guess = predicted_reward(model, row, weights)
        if truth is None or guess is None:
            continue
        actual.append(truth)
        predicted.append(guess)

    return {
        "n": len(actual),
        "spearman": spearman(actual, predicted),
        "ndcg@5": ndcg_at_k(actual, predicted, 5),
        "precision@3": precision_at_k(actual, predicted, 3),
        "per_target": per_target_ranking(model, rows),
    }


def per_target_ranking(
    model: MultiTaskModel, rows: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    """Rank correlation for each head against its *own* label.

    The headline `spearman` above is deliberately end-to-end: it grades a
    prediction against the reward the channel actually earned, across every
    metric that was synced. That is the question Brain exists to answer, but
    it conflates two things when a model has only some of the heads — and
    per-row reward composition shifts with whatever happened to be synced.

    This is the narrower, cleaner question: on the one quantity this head
    does model, does it put the videos in the right order? A head can be
    useful here and still not move the end-to-end number yet.
    """
    out: dict[str, Any] = {}
    for target, head in model.heads.items():
        features, labels, _ = ds.to_matrix(rows, target)
        if len(labels) < 3:
            continue
        out[target] = {
            "n": len(labels),
            "spearman": spearman(labels, head.predict_many(features)),
        }
    return out


def evaluate_model(
    model: MultiTaskModel,
    rows: Sequence[dict[str, Any]],
    weights: dict[str, float],
) -> dict[str, Any]:
    """Full evaluation: one block per head, plus the ranking block."""
    heads: dict[str, dict[str, float]] = {}
    for target in TARGETS:
        head = model.heads.get(target)
        if not head:
            continue
        features, labels, _ = ds.to_matrix(rows, target)
        if not features:
            continue
        predictions = head.predict_many(features)
        heads[target] = regression_metrics(labels, predictions)

    return {
        "heads": heads,
        "ranking": ranking_metrics(model, rows, weights),
        "rows": len(rows),
    }


def baseline_comparison(
    train_rows: Sequence[dict[str, Any]],
    eval_rows: Sequence[dict[str, Any]],
    target: str,
) -> dict[str, float]:
    """The null model: predict the *training* mean on the evaluation rows.

    This used to average the evaluation rows themselves, which quietly made
    the baseline an oracle — the best constant achievable on the very data
    being predicted, a number no honest forecaster can see. Heads that did
    beat a real null model were being dropped for failing to beat one that
    had read the answer.

    The null model in forecasting is always "carry the history forward".
    """
    _, train_labels, _ = ds.to_matrix(train_rows, target)
    _, eval_labels, _ = ds.to_matrix(eval_rows, target)
    if not train_labels or not eval_labels:
        return {"n": 0, "mae": 0.0, "rmse": 0.0, "r2": 0.0, "residual_std": 0.0}
    mean = sum(train_labels) / len(train_labels)
    return regression_metrics(eval_labels, [mean] * len(eval_labels))
