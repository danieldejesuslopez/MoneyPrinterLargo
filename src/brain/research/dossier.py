"""What Brain tells Claude about itself before asking for advice.

A research advisor is only as good as its briefing. An LLM asked "how do I
improve my model?" with no context produces a textbook list — more data,
try gradient boosting, add embeddings — none of it grounded in *this* model's
actual failure. So the dossier carries the measurements a person would ask
for first:

  - how the model performs right now, against both null models, in both eras;
  - which features carry signal *within a format*, with the noise threshold
    beside each number so a 0.18 is not mistaken for a finding;
  - what is measured and what is missing;
  - every experiment already tried and how it ended;
  - the exact menu of changes the loop can actually run.

Everything is aggregated. Six example titles are the only raw content, and
they are the user's own public video titles.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Sequence

from ..constants import TARGET_NORMALIZED_VIEWS
from ..data import validators
from ..data import dataset as ds
from ..features import registry as fr
from ..models import registry as model_registry
from ..training import evaluate as ev
from . import bench, knobs, ledger


def _round(value: Any, digits: int = 3) -> Any:
    if isinstance(value, float):
        return round(value, digits)
    return value


def channel_summary(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    kinds = defaultdict(list)
    for row in rows:
        kinds[row.get("kind")].append(row.get("raw_views") or 0)
    dates = sorted(str(row.get("published_at") or "")[:10] for row in rows if row.get("published_at"))

    def median(values):
        ordered = sorted(values)
        return ordered[len(ordered) // 2] if ordered else None

    return {
        "videos": len(rows),
        "by_format": {
            kind: {"count": len(values), "median_views": median(values)}
            for kind, values in kinds.items()
        },
        "first_published": dates[0] if dates else None,
        "last_published": dates[-1] if dates else None,
    }


def within_format_signal(
    rows: Sequence[dict[str, Any]], kind: str = "short", top: int = 12
) -> dict[str, Any]:
    """Rank correlation of each feature with the label, inside one format.

    Across formats the correlations are dominated by the format gap itself,
    which a format-aware null already knows. Within a format is where any
    remaining, usable signal has to live.
    """
    subset = [row for row in rows if row.get("kind") == kind]
    x, y, _ = ds.to_matrix(subset, TARGET_NORMALIZED_VIEWS)
    if len(y) < 10:
        return {"format": kind, "n": len(y), "features": []}

    threshold = 1.96 / math.sqrt(len(y) - 3)
    scored = []
    for index, name in enumerate(fr.FEATURE_NAMES):
        column = [row[index] for row in x]
        if len(set(column)) < 3:
            continue
        rho = ev.spearman(y, column)
        if rho is not None:
            scored.append((abs(rho), rho, name))
    scored.sort(reverse=True)
    return {
        "format": kind,
        "n": len(y),
        "noise_threshold": round(threshold, 3),
        "features": [
            {
                "name": name,
                "group": fr.group_of(name),
                "rho": round(rho, 3),
                "above_noise": magnitude > threshold,
            }
            for magnitude, rho, name in scored[:top]
        ],
    }


def coverage_by_group(rows: Sequence[dict[str, Any]]) -> dict[str, float]:
    """Share of each group's features actually measured, averaged over rows."""
    sizes = defaultdict(int)
    for spec in fr.INPUT_SPECS:
        sizes[spec.group] += 1
    missing = defaultdict(int)
    for row in rows:
        for name in row.get("missing_features") or []:
            missing[fr.group_of(name)] += 1
    count = max(1, len(rows))
    return {
        group: round(1.0 - (missing[group] / count) / size, 3)
        for group, size in sorted(sizes.items())
    }


def examples(rows: Sequence[dict[str, Any]], kind: str = "short", count: int = 3) -> dict[str, Any]:
    subset = [
        row for row in rows
        if row.get("kind") == kind
        and (row.get("targets") or {}).get(TARGET_NORMALIZED_VIEWS) is not None
    ]
    subset.sort(key=lambda row: row["targets"][TARGET_NORMALIZED_VIEWS], reverse=True)

    def shape(row):
        features = row.get("features") or {}
        return {
            "title": str(row.get("title") or "")[:90],
            "normalized_views": round(row["targets"][TARGET_NORMALIZED_VIEWS], 2),
            "thumb_edge_density": _round(features.get("thumb_edge_density")),
            "thumb_text_bands": _round(features.get("thumb_text_bands")),
            "title_visible_chars": _round(features.get("title_visible_chars")),
        }

    return {
        "best": [shape(row) for row in subset[:count]],
        "worst": [shape(row) for row in subset[-count:]],
    }


def model_summary() -> dict[str, Any]:
    champion = model_registry.champion()
    if not champion:
        return {"champion": None}
    return {
        "champion": champion.version,
        "tier": champion.tier,
        "heads": {
            target: {k: _round(v) for k, v in stats.items() if k in ("rmse", "r2", "n")}
            for target, stats in champion.calibration.items()
        },
    }


def current_performance(current: bench.Evaluation) -> dict[str, Any]:
    """The current configuration's numbers, overall and per era."""
    order = list(range(current.scored))
    cut = int(round(len(order) * bench.SEARCH_SHARE))
    return {
        "overall": bench.summarize(current),
        "search_era": bench.summarize(current, order[:cut]),
        "confirmation_era": bench.summarize(current, order[cut:]),
    }


def build_dossier(
    base_rows: Sequence[dict[str, Any]],
    current: bench.Evaluation,
    objective: str = "format",
    max_experiments: int = 4,
) -> dict[str, Any]:
    confirm_pairs = [(i, i) for i in range(current.scored)]
    cut = int(round(len(confirm_pairs) * bench.SEARCH_SHARE))
    era = bench.era_signature(confirm_pairs[cut:], current)
    used = ledger.experiments_on_era(era)
    gaps = validators.data_gaps()

    return {
        "objective": {
            "metric": (
                "format_gain_pct: % by which the model's RMSE undercuts predicting "
                "the historical mean of the same format (Short vs long-form)"
                if objective == "format" else
                "global_gain_pct: % by which the model's RMSE undercuts predicting "
                "the historical mean of all videos"
            ),
            "why": (
                "The global null is already beaten comfortably, mostly by knowing "
                "that Shorts and long-form differ. The open problem is predicting "
                "which video beats another video of the same format."
            ),
        },
        "evaluation_protocol": {
            "method": "walk-forward: each video predicted by a model trained only on "
                      "videos published before it",
            "search_share": round(bench.SEARCH_SHARE, 3),
            "min_search_gain_pp": bench.MIN_SEARCH_GAIN,
            "min_confirm_probability": bench.MIN_CONFIRM_PROBABILITY,
            "confirmation_era_experiments_used": used,
            "confirmation_era_budget": ledger.ERA_BUDGET,
        },
        "channel": channel_summary(base_rows),
        "current_performance": current_performance(current),
        "model": model_summary(),
        "feature_coverage_by_group": coverage_by_group(base_rows),
        "signal_within_shorts": within_format_signal(base_rows, "short"),
        "examples_within_shorts": examples(base_rows, "short"),
        "data_gaps": gaps.get("notes", []),
        "knobs": [knob.describe() for knob in knobs.KNOBS],
        "approved_overrides": knobs.approved_overrides()[0],
        "history": ledger.history(),
        "max_experiments": max_experiments,
    }
