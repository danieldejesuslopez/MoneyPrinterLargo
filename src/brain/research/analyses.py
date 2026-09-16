"""What Claude can ask Brain to compute during a research conversation.

In a session Claude drives the investigation: it reads a result, forms a
hypothesis, and asks for the next piece of evidence. Brain answers with real
numbers from the user's own videos.

Like the knob menu, this is a closed list. Claude names an analysis and passes
parameters; Brain validates them and runs its own code. There is no path from
anything Claude writes to anything that executes, so a long unattended
conversation cannot drift into doing something nobody reviewed.

Every analysis except `experiment` reuses the walk-forward computed once at
the start of the session, so it costs milliseconds. `experiment` retrains the
model through the full walk-forward, takes minutes, and spends the shared
confirmation budget - which is why it is the only one Claude is told to use
sparingly.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from ..constants import TARGET_NORMALIZED_VIEWS
from ..data import dataset as ds
from ..data import normalizer as norm
from ..features import registry as fr
from ..memory import channel_memory
from ..models import registry as model_registry
from ..schema import VideoRecord, parse_date
from ..training import evaluate as ev
from . import bench, knobs, ledger

TARGET = TARGET_NORMALIZED_VIEWS

#: Features shown beside every video, chosen because they are the ones with
#: measurable signal inside Shorts or define the label's scale.
KEY_FEATURES = (
    "thumb_edge_density", "thumb_text_bands", "thumb_center_energy",
    "thumb_brightness", "title_visible_chars", "title_hashtags",
    "channel_format_level", "channel_median_views_log",
)


@dataclass
class Context:
    """Everything an analysis may read, computed once per session."""

    rows: list[dict[str, Any]]
    current: bench.Evaluation
    rows_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.rows_by_id = {str(row.get("video_id")): row for row in self.rows}


class AnalysisError(ValueError):
    """Bad parameters; the message is sent back to Claude verbatim."""


def _int(params: dict[str, Any], key: str, default: int, low: int, high: int) -> int:
    value = params.get(key, default)
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise AnalysisError(f"{key} must be an integer between {low} and {high}")
    if not low <= number <= high:
        raise AnalysisError(f"{key} must be between {low} and {high}, got {number}")
    return number


def _choice(params: dict[str, Any], key: str, default: str, allowed: Sequence[str]) -> str:
    value = str(params.get(key, default))
    if value not in allowed:
        raise AnalysisError(f"{key} must be one of {list(allowed)}, got {value!r}")
    return value


def _round(value: Any, digits: int = 3) -> Any:
    return round(value, digits) if isinstance(value, float) else value


def _real(value: float) -> float:
    """Label space (log1p) back to a channel-relative view multiple."""
    return round(ds.inverse_target(TARGET, value), 3)


def _feature_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    features = row.get("features") or {}
    missing = set(row.get("missing_features") or ())
    return {
        name: (None if name in missing else _round(features.get(name)))
        for name in KEY_FEATURES
    }


def _in_format(ctx: Context, index: int, fmt: str) -> bool:
    return fmt == "all" or ctx.current.kinds[index] == fmt


# -- the analyses ----------------------------------------------------------


def worst_predictions(ctx: Context, params: dict[str, Any]) -> dict[str, Any]:
    count = _int(params, "count", 8, 1, 15)
    direction = _choice(params, "direction", "both", ("over", "under", "both"))
    fmt = _choice(params, "format", "short", ("short", "long", "all"))

    items = []
    for i in range(ctx.current.scored):
        if not _in_format(ctx, i, fmt):
            continue
        residual = ctx.current.predicted[i] - ctx.current.actual[i]
        if direction == "over" and residual <= 0:
            continue
        if direction == "under" and residual >= 0:
            continue
        row = ctx.rows_by_id.get(ctx.current.video_ids[i]) or {}
        items.append((abs(residual), {
            "title": str(row.get("title") or "")[:90],
            "published": str(row.get("published_at") or "")[:10],
            "actual_multiple": _real(ctx.current.actual[i]),
            "predicted_multiple": _real(ctx.current.predicted[i]),
            "format_mean_multiple": _real(ctx.current.format_null[i]),
            "residual_log": round(residual, 3),
            "features": _feature_snapshot(row),
        }))
    items.sort(key=lambda item: item[0], reverse=True)
    return {
        "note": "residual_log > 0 means the model predicted too high",
        "videos": [item for _, item in items[:count]],
    }


def drift(ctx: Context, params: dict[str, Any]) -> dict[str, Any]:
    periods = _int(params, "periods", 3, 2, 6)
    scored = ctx.current.scored
    if scored < periods * 4:
        raise AnalysisError(f"only {scored} scored videos; use fewer periods")

    size = scored // periods
    out = []
    for p in range(periods):
        start = p * size
        stop = scored if p == periods - 1 else (p + 1) * size
        picks = list(range(start, stop))
        rows = [ctx.rows_by_id.get(ctx.current.video_ids[i]) or {} for i in picks]
        summary = bench.summarize(ctx.current, picks)
        feature_means = {}
        for name in KEY_FEATURES:
            values = [
                (r.get("features") or {}).get(name) for r in rows
                if name not in set(r.get("missing_features") or ())
            ]
            values = [v for v in values if isinstance(v, (int, float))]
            feature_means[name] = _round(sum(values) / len(values)) if values else None
        raw_views = sorted(r.get("raw_views") or 0 for r in rows)
        out.append({
            "period": p + 1,
            "from": str(rows[0].get("published_at") or "")[:10] if rows else None,
            "to": str(rows[-1].get("published_at") or "")[:10] if rows else None,
            **summary,
            "mean_actual_multiple": _real(sum(ctx.current.actual[i] for i in picks) / len(picks)),
            "mean_predicted_multiple": _real(sum(ctx.current.predicted[i] for i in picks) / len(picks)),
            "median_raw_views": raw_views[len(raw_views) // 2] if raw_views else None,
            "shorts_share": round(sum(1 for i in picks if ctx.current.kinds[i] == "short") / len(picks), 3),
            "feature_means": feature_means,
        })
    return {"periods": out}


def residual_correlations(ctx: Context, params: dict[str, Any]) -> dict[str, Any]:
    top = _int(params, "top", 12, 3, 30)
    fmt = _choice(params, "format", "short", ("short", "all"))

    picks = [i for i in range(ctx.current.scored) if _in_format(ctx, i, fmt)]
    residuals = [ctx.current.predicted[i] - ctx.current.actual[i] for i in picks]
    rows = [ctx.rows_by_id.get(ctx.current.video_ids[i]) or {} for i in picks]
    if len(picks) < 8:
        raise AnalysisError("too few scored videos in this format")

    threshold = 1.96 / max(1.0, (len(picks) - 3) ** 0.5)
    scored = []
    for name in fr.FEATURE_NAMES:
        column = [float((r.get("features") or {}).get(name, 0.0)) for r in rows]
        if len(set(column)) < 3:
            continue
        rho = ev.spearman(residuals, column)
        if rho is not None:
            scored.append((abs(rho), name, rho))
    scored.sort(reverse=True)
    return {
        "n": len(picks),
        "noise_threshold": round(threshold, 3),
        "note": "positive rho: the model over-predicts as the feature grows",
        "features": [
            {"name": name, "group": fr.group_of(name), "rho": round(rho, 3),
             "above_noise": magnitude > threshold}
            for magnitude, name, rho in scored[:top]
        ],
    }


def feature_profile(ctx: Context, params: dict[str, Any]) -> dict[str, Any]:
    name = str(params.get("feature") or "")
    if name not in fr.FEATURE_NAMES:
        raise AnalysisError(f"unknown feature {name!r}; ask for feature_list first")
    bins = _int(params, "bins", 4, 2, 6)
    fmt = _choice(params, "format", "short", ("short", "all"))

    scored_index = ctx.current.index()
    points = []
    for row in ctx.rows:
        if fmt != "all" and row.get("kind") != fmt:
            continue
        label = (row.get("targets") or {}).get(TARGET)
        if label is None or name in set(row.get("missing_features") or ()):
            continue
        value = float((row.get("features") or {}).get(name, 0.0))
        i = scored_index.get(str(row.get("video_id")))
        residual = (ctx.current.predicted[i] - ctx.current.actual[i]) if i is not None else None
        points.append((value, ds.transform_target(TARGET, label), residual))
    if len(points) < bins * 3:
        raise AnalysisError(f"only {len(points)} videos have {name}; use fewer bins")

    points.sort(key=lambda item: item[0])
    size = len(points) // bins
    out = []
    for b in range(bins):
        chunk = points[b * size:] if b == bins - 1 else points[b * size:(b + 1) * size]
        residuals = [r for _, _, r in chunk if r is not None]
        out.append({
            "bin": b + 1,
            "range": [_round(chunk[0][0]), _round(chunk[-1][0])],
            "n": len(chunk),
            "mean_multiple": _real(sum(label for _, label, _ in chunk) / len(chunk)),
            "mean_residual_log": _round(sum(residuals) / len(residuals)) if residuals else None,
        })
    return {"feature": name, "description": fr.describe(name), "format": fmt, "bins": out}


def feature_list(ctx: Context, params: dict[str, Any]) -> dict[str, Any]:
    group = params.get("group")
    groups = knobs.all_groups()
    if group is not None and group not in groups:
        raise AnalysisError(f"group must be one of {groups}")
    missing_counts: dict[str, int] = {}
    for row in ctx.rows:
        for name in row.get("missing_features") or ():
            missing_counts[name] = missing_counts.get(name, 0) + 1
    total = max(1, len(ctx.rows))
    return {
        "features": [
            {"name": spec.name, "group": spec.group, "description": spec.description,
             "coverage": round(1 - missing_counts.get(spec.name, 0) / total, 2)}
            for spec in fr.INPUT_SPECS
            if group is None or spec.group == group
        ]
    }


def topic_clusters(ctx: Context, params: dict[str, Any]) -> dict[str, Any]:
    top = _int(params, "top", 8, 3, 12)
    videos = [
        VideoRecord(
            video_id=str(row.get("video_id")), title=str(row.get("title") or ""),
            topic=str(row.get("topic") or ""), views=row.get("raw_views"),
            published_at=parse_date(row.get("published_at")),
        )
        for row in ctx.rows
    ]
    return {"clusters": channel_memory.cluster_topics(videos)[:top]}


def calendar(ctx: Context, params: dict[str, Any]) -> dict[str, Any]:
    fmt = _choice(params, "format", "short", ("short", "all"))
    by_day: dict[int, list[float]] = {}
    by_hour: dict[str, list[float]] = {}
    for row in ctx.rows:
        if fmt != "all" and row.get("kind") != fmt:
            continue
        label = (row.get("targets") or {}).get(TARGET)
        when = parse_date(row.get("published_at"))
        if label is None or when is None:
            continue
        by_day.setdefault(when.weekday(), []).append(label)
        bucket = f"{(when.hour // 6) * 6:02d}-{(when.hour // 6) * 6 + 5:02d}h"
        by_hour.setdefault(bucket, []).append(label)

    def shape(groups):
        return {
            str(key): {"n": len(values), "median_multiple": round(norm.median(values), 3)}
            for key, values in sorted(groups.items())
        }

    days = ["lun", "mar", "mie", "jue", "vie", "sab", "dom"]
    return {
        "note": "hours are as stored by the importer (local time of the import machine)",
        "weekday": {days[int(k)]: v for k, v in shape(by_day).items()},
        "hour_block": shape(by_hour),
    }


def title_examples(ctx: Context, params: dict[str, Any]) -> dict[str, Any]:
    group = _choice(params, "group", "best", ("best", "worst"))
    count = _int(params, "count", 6, 3, 12)
    fmt = _choice(params, "format", "short", ("short", "all"))
    rows = [
        row for row in ctx.rows
        if (fmt == "all" or row.get("kind") == fmt)
        and (row.get("targets") or {}).get(TARGET) is not None
    ]
    rows.sort(key=lambda row: row["targets"][TARGET], reverse=(group == "best"))
    return {
        "group": group,
        "videos": [
            {"title": str(row.get("title") or "")[:100],
             "multiple": round(row["targets"][TARGET], 3),
             "published": str(row.get("published_at") or "")[:10],
             "features": _feature_snapshot(row)}
            for row in rows[:count]
        ],
    }


def model_weights(ctx: Context, params: dict[str, Any]) -> dict[str, Any]:
    top = _int(params, "top", 12, 3, 30)
    champion = model_registry.champion()
    head = champion.heads.get(TARGET) if champion else None
    if head is None:
        raise AnalysisError("there is no trained champion with a normalized_views head")
    return {
        "model": champion.version,
        "note": "weights on standardized features; sign is the direction of the effect",
        "weights": [
            {"name": name, "group": fr.group_of(name), "weight": round(weight, 4)}
            for name, weight in head.importance()[:top]
        ],
    }


def duplicates(ctx: Context, params: dict[str, Any]) -> dict[str, Any]:
    """Topics published more than once, and what the later copies earned.

    Built for the question a first session raised on its own: several topic
    clusters turned out to be the same video uploaded again. Whether a
    repeat cannibalises the original or rides its momentum is a decision the
    creator makes on every upload, and it also contaminates the label - a
    copy published three days after the first is not an independent sample.
    """
    from ..features import lexicon as lx

    threshold = params.get("threshold", 0.5)
    try:
        threshold = float(threshold)
    except (TypeError, ValueError):
        raise AnalysisError("threshold must be a number between 0.3 and 0.8")
    if not 0.3 <= threshold <= 0.8:
        raise AnalysisError("threshold must be between 0.3 and 0.8")

    rows = sorted(
        (r for r in ctx.rows if (r.get("targets") or {}).get(TARGET) is not None),
        key=lambda r: str(r.get("published_at") or ""),
    )
    groups: list[dict[str, Any]] = []
    for row in rows:
        tokens = lx.content_tokens(lx.visible_prefix(str(row.get("title") or "")))
        for group in groups:
            if tokens and lx.jaccard(tokens, group["tokens"]) >= threshold:
                group["rows"].append(row)
                group["tokens"] |= tokens
                break
        else:
            groups.append({"tokens": set(tokens), "rows": [row]})

    repeated = [g for g in groups if len(g["rows"]) > 1]
    firsts, repeats = [], []
    out = []
    for group in repeated:
        first_date = parse_date(group["rows"][0].get("published_at"))
        copies = []
        for position, row in enumerate(group["rows"]):
            when = parse_date(row.get("published_at"))
            multiple = row["targets"][TARGET]
            (firsts if position == 0 else repeats).append(multiple)
            copies.append({
                "copy": position + 1,
                "title": str(row.get("title") or "")[:80],
                "published": str(row.get("published_at") or "")[:10],
                "days_after_first": (
                    round((when - first_date).total_seconds() / 86400.0, 1)
                    if when and first_date else None
                ),
                "format": row.get("kind"),
                "raw_views": row.get("raw_views"),
                "multiple": round(multiple, 3),
            })
        out.append({"copies": copies})

    return {
        "threshold": threshold,
        "videos": len(rows),
        "repeated_topics": len(repeated),
        "videos_in_repeats": sum(len(g["rows"]) for g in repeated),
        "median_multiple_first_copy": _round(norm.median(firsts)) if firsts else None,
        "median_multiple_later_copies": _round(norm.median(repeats)) if repeats else None,
        "note": "tiny sample: compare copies within each group, not only the medians",
        "groups": out,
    }


def compare_groups(ctx: Context, params: dict[str, Any]) -> dict[str, Any]:
    """Split videos on one feature and test whether the halves really differ.

    Correlations tell Claude that a feature and the label move together;
    content advice needs something stronger - "covers below this busyness
    earn X times more, and here is how sure that is". The difference in mean
    log-label comes with a bootstrap 95% interval, so a guideline can be
    reported as proven only when the interval excludes zero.
    """
    import random

    name = str(params.get("feature") or "")
    if name not in fr.FEATURE_NAMES:
        raise AnalysisError(f"unknown feature {name!r}; ask for feature_list first")
    split = _choice(params, "split", "median", ("median", "quartiles"))
    fmt = _choice(params, "format", "short", ("short", "all"))

    points = sorted(
        (
            (float((row.get("features") or {}).get(name, 0.0)),
             ds.transform_target(TARGET, row["targets"][TARGET]))
            for row in ctx.rows
            if (fmt == "all" or row.get("kind") == fmt)
            and (row.get("targets") or {}).get(TARGET) is not None
            and name not in set(row.get("missing_features") or ())
        ),
        key=lambda item: item[0],
    )
    if len(points) < 16:
        raise AnalysisError(f"only {len(points)} videos have {name} in this format")

    if split == "median":
        half = len(points) // 2
        low, high = points[:half], points[half:]
    else:
        quarter = len(points) // 4
        low, high = points[:quarter], points[-quarter:]

    low_labels = [label for _, label in low]
    high_labels = [label for _, label in high]
    difference = sum(high_labels) / len(high_labels) - sum(low_labels) / len(low_labels)

    rng = random.Random(20260912)
    draws = []
    for _ in range(2000):
        a = [low_labels[rng.randrange(len(low_labels))] for _ in low_labels]
        b = [high_labels[rng.randrange(len(high_labels))] for _ in high_labels]
        draws.append(sum(b) / len(b) - sum(a) / len(a))
    draws.sort()
    ci_low, ci_high = draws[50], draws[1949]

    def describe(group):
        labels = [label for _, label in group]
        return {
            "n": len(group),
            "feature_range": [_round(group[0][0]), _round(group[-1][0])],
            "median_multiple": _real(norm.median(labels)),
        }

    low_summary, high_summary = describe(low), describe(high)
    low_median = low_summary["median_multiple"]
    return {
        "feature": name,
        "description": fr.describe(name),
        "split": split,
        "low_group": low_summary,
        "high_group": high_summary,
        # The label is log1p(multiple), so this is a difference of log(1 + x),
        # not a log view ratio; the ratio of medians below is the readable one.
        "high_minus_low_mean_log1p": round(difference, 3),
        "high_minus_low_ci95": [round(ci_low, 3), round(ci_high, 3)],
        "median_multiple_ratio_high_over_low": (
            round(high_summary["median_multiple"] / low_median, 3) if low_median else None
        ),
        "distinguishable_from_zero": ci_low > 0 or ci_high < 0,
    }


def experiment_budget(current: bench.Evaluation) -> tuple[str, int]:
    """(confirmation era id, experiments still allowed on it)."""
    pairs = [(i, i) for i in range(current.scored)]
    cut = int(round(len(pairs) * bench.SEARCH_SHARE))
    era = bench.era_signature(pairs[cut:], current)
    return era, max(0, ledger.ERA_BUDGET - ledger.experiments_on_era(era))


def experiment(ctx: Context, params: dict[str, Any]) -> dict[str, Any]:
    changes, errors = knobs.validate(params.get("changes"))
    if errors:
        raise AnalysisError("; ".join(errors))
    if not changes:
        raise AnalysisError("changes must name at least one knob")
    if ledger.canonical(changes) in ledger.tried_change_sets():
        raise AnalysisError("this exact change set was already tried; see history")
    _, remaining = experiment_budget(ctx.current)
    if remaining <= 0:
        raise AnalysisError(
            "the confirmation budget is exhausted: no more experiments until new "
            "videos are published"
        )

    candidate = bench.evaluate(changes, ctx.rows)
    comparison = bench.compare(ctx.current, candidate, "format")
    result = {
        "name": str(params.get("name") or "session_experiment")[:48],
        "hypothesis": str(params.get("hypothesis") or "")[:600],
        "changes": changes,
        "overall": bench.summarize(candidate),
        "comparison": comparison,
    }
    # Recorded immediately, not at the end of the session: if the session
    # dies halfway, the budget must still know this experiment happened.
    ledger.record_round({
        "round_id": "s" + _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d%H%M%S%f"),
        "created_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "source": "session",
        "results": [result],
    })
    return result


@dataclass(frozen=True)
class Analysis:
    name: str
    fn: Callable[[Context, dict[str, Any]], dict[str, Any]]
    params: str
    description: str
    expensive: bool = False


MENU: tuple[Analysis, ...] = (
    Analysis("worst_predictions", worst_predictions,
             '{"count": 1-15, "direction": "over|under|both", "format": "short|long|all"}',
             "Videos the model got most wrong, with their key features."),
    Analysis("drift", drift, '{"periods": 2-6}',
             "Model performance, label level and feature means per chronological period."),
    Analysis("residual_correlations", residual_correlations,
             '{"top": 3-30, "format": "short|all"}',
             "Which features correlate with the model's errors: what it systematically misses."),
    Analysis("feature_profile", feature_profile,
             '{"feature": "<name>", "bins": 2-6, "format": "short|all"}',
             "Label and residual across quantile bins of one feature."),
    Analysis("feature_list", feature_list, '{"group": "<optional group>"}',
             "Every feature name, group, description and coverage."),
    Analysis("topic_clusters", topic_clusters, '{"top": 3-12}',
             "Topic families and their performance relative to the channel."),
    Analysis("calendar", calendar, '{"format": "short|all"}',
             "Median performance by weekday and six-hour block."),
    Analysis("title_examples", title_examples,
             '{"group": "best|worst", "count": 3-12, "format": "short|all"}',
             "Best or worst videos by label, with titles and key features."),
    Analysis("model_weights", model_weights, '{"top": 3-30}',
             "The champion model's strongest weights for the views head."),
    Analysis("duplicates", duplicates, '{"threshold": 0.3-0.8}',
             "Topics published more than once: each copy's date, gap, format and result."),
    Analysis("compare_groups", compare_groups,
             '{"feature": "<name>", "split": "median|quartiles", "format": "short|all"}',
             "Split videos on one feature and test the difference with a 95% interval."),
    Analysis("experiment", experiment,
             '{"name": "slug", "hypothesis": "...", "changes": {"<knob>": value}}',
             "Retrain through the full walk-forward with knob changes and compare on "
             "search and confirmation eras. Takes minutes and spends the shared budget.",
             expensive=True),
)

MENU_BY_NAME: dict[str, Analysis] = {analysis.name: analysis for analysis in MENU}


def describe_menu() -> list[dict[str, str]]:
    return [
        {"analysis": a.name, "params": a.params, "what": a.description,
         **({"cost": "minutes, spends budget"} if a.expensive else {})}
        for a in MENU
    ]


def run(ctx: Context, name: str, params: Any) -> dict[str, Any]:
    """Run one analysis. Raises AnalysisError for anything Claude got wrong."""
    analysis = MENU_BY_NAME.get(str(name))
    if analysis is None:
        raise AnalysisError(f"unknown analysis {name!r}; choose from {sorted(MENU_BY_NAME)}")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise AnalysisError("params must be a JSON object")
    return analysis.fn(ctx, params)
