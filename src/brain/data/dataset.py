"""Building the training dataset: one row per published video.

The central trick is `candidate_from_record`: a published video is replayed
as the `Candidate` it *was* before upload, and its features are extracted
against the channel history as it stood at that moment. Train and inference
therefore run the exact same code path — the model never sees a column at
training time that it would not have at decision time.

Rows are stored as JSONL rather than Parquet. The plan calls for Parquet
(spec §11), and that is the right destination at tens of thousands of rows;
at the scale this project is actually at, JSONL costs nothing, adds no
dependency, is diffable, and can be converted in one pass later.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from typing import Any, Iterable

from ..config import cache_file
from ..constants import (
    DATASET_FILE,
    DATASET_SCHEMA_VERSION,
    LOG_TARGETS,
    TARGETS,
)
from ..features import registry as fr
from ..schema import Candidate, ChannelContext, VideoRecord, now_utc, parse_date
from . import collector
from . import normalizer as norm


#: Feature names forced to a constant in every design matrix. A constant
#: column has zero variance, so its fitted weight is exactly zero and the
#: feature contributes nothing - at training *and* at inference, where the
#: real value meets that zero weight. This is how a feature group is switched
#: off for an experiment without changing the schema every model depends on.
EXCLUDED_FEATURES: frozenset[str] = frozenset()


def dataset_path() -> str:
    return cache_file(DATASET_FILE)


def candidate_from_record(record: VideoRecord) -> Candidate:
    """Replay a published video as the candidate it was before upload.

    Only fields that existed *before* publishing are copied. `script` and
    `hook` are read from the cache entry when the pipeline saved them and
    are simply absent otherwise — which is why `validators.data_gaps()`
    reports script persistence as the highest-value thing to start
    recording.
    """
    raw = record.raw or {}
    hook = str(raw.get("hook") or "")
    if not hook:
        variants = raw.get("hook_variants")
        if isinstance(variants, list) and variants:
            first = variants[0]
            if isinstance(first, dict):
                hook = str(first.get("hook") or "")

    return Candidate(
        candidate_id=record.video_id,
        topic=record.topic,
        title=record.title,
        hook=hook,
        script=str(raw.get("script") or ""),
        description=record.description,
        tags=list(raw.get("tags") or []),
        kind="short" if record.is_short else "long",
        target_duration_seconds=record.duration_seconds,
        thumbnail_path=record.thumbnail_path,
        image_style=str(raw.get("image_style") or ""),
        voice=str(raw.get("voice") or ""),
        retention_mode=record.retention_mode,
        planned_publish_at=record.published_at,
        production_cost=record.production_cost,
        retention_score=record.retention_score,
        hook_score=record.hook_score,
        first_image_score=record.first_image_score,
        meta={
            "is_remix": record.is_remix,
            "hook_variants": raw.get("hook_variants") or [],
        },
    )


def global_median_views(channels: list[ChannelContext]) -> float | None:
    """Median views across your own channels - the cold-start fallback.

    Reference channels are excluded on purpose. This baseline only fires for
    a video with too few prior peers to normalize against, and a 3M-subscriber
    channel in the pool would put the fallback median in the hundreds of
    thousands - labelling the first videos of a small channel as catastrophic
    failures when they were simply early.
    """
    own = [c for c in channels if not c.is_reference] or list(channels)
    views = [
        video.views
        for channel in own
        for video in channel.published()
        if video.views is not None
    ]
    return norm.median(views)


def build_row(
    record: VideoRecord,
    channel: ChannelContext,
    global_median: float | None = None,
    snapshots: dict[str, list[dict[str, Any]]] | None = None,
    now: _dt.datetime | None = None,
) -> dict[str, Any]:
    """One training row: features as of publication, labels as of today."""
    moment = now or now_utc()
    candidate = candidate_from_record(record)
    vector = fr.vectorize(candidate, channel)

    peers = norm.peer_window(record, channel.published())
    labels = norm.build_labels(record, peers, global_median)

    video_snapshots = (snapshots or {}).get(record.video_id) or []
    windowed = collector.windowed_views(video_snapshots)

    return {
        "row_version": DATASET_SCHEMA_VERSION,
        "feature_schema": fr.SCHEMA_HASH,
        "video_id": record.video_id,
        "account_id": record.account_id,
        "is_reference": bool(channel.is_reference),
        "url": record.url,
        "title": record.title,
        "topic": record.topic,
        "published_at": record.published_at.isoformat() if record.published_at else None,
        "kind": candidate.kind,
        "age_days": record.age_days(moment),
        "features": vector.as_dict(),
        "missing_features": vector.missing,
        "coverage": round(vector.coverage, 4),
        "targets": labels["targets"],
        "success_class": labels["success_class"],
        "normalization_basis": labels["normalization_basis"],
        "peer_count": labels["peer_count"],
        "watch_minutes_derived": labels["watch_minutes_derived"],
        "mature": norm.is_mature(record, moment),
        "windowed_views": windowed,
        "velocity": collector.view_velocity(windowed),
        "raw_views": record.views,
    }


def build_rows(
    channels: list[ChannelContext] | None = None,
    now: _dt.datetime | None = None,
) -> list[dict[str, Any]]:
    """Every usable training row across every channel, oldest first."""
    from ..research import knobs

    with knobs.configured():
        return _build_rows(channels, now)


def _build_rows(
    channels: list[ChannelContext] | None,
    now: _dt.datetime | None,
) -> list[dict[str, Any]]:
    channels = channels if channels is not None else collector.load_channels(
        now=now, include_reference=True
    )
    global_median = global_median_views(channels)
    snapshots = collector.snapshots_by_video()

    rows: list[dict[str, Any]] = []
    for channel in channels:
        for record in channel.published():
            if not record.published_at:
                continue
            rows.append(build_row(record, channel, global_median, snapshots, now))
    rows.sort(key=lambda r: str(r.get("published_at") or ""))
    return rows


def build_dataset(
    channels: list[ChannelContext] | None = None,
    path: str | None = None,
    now: _dt.datetime | None = None,
) -> dict[str, Any]:
    """Build and persist the dataset. Returns a summary of what it contains."""
    rows = build_rows(channels, now)
    target = path or dataset_path()
    with open(target, "w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
    return {"path": target, **summarize(rows)}


def load_dataset(path: str | None = None) -> list[dict[str, Any]]:
    target = path or dataset_path()
    if not os.path.exists(target):
        return []
    rows: list[dict[str, Any]] = []
    with open(target, "r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    rows.sort(key=lambda r: str(r.get("published_at") or ""))
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Row counts, label availability per target, and coverage."""
    labeled: dict[str, int] = {target: 0 for target in TARGETS}
    for row in rows:
        for target, value in (row.get("targets") or {}).items():
            if value is not None and target in labeled:
                labeled[target] += 1
    mature = [row for row in rows if row.get("mature")]
    coverages = [row.get("coverage") or 0.0 for row in rows]
    reference_rows = sum(1 for row in rows if row.get("is_reference"))
    return {
        "rows": len(rows),
        "mature_rows": len(mature),
        "own_rows": len(rows) - reference_rows,
        "reference_rows": reference_rows,
        "labeled_per_target": labeled,
        "mean_coverage": round(sum(coverages) / len(coverages), 4) if coverages else 0.0,
        "channels": len({row.get("account_id") for row in rows}),
        "first_published": rows[0].get("published_at") if rows else None,
        "last_published": rows[-1].get("published_at") if rows else None,
    }


def time_split(
    rows: list[dict[str, Any]],
    validation_fraction: float = 0.2,
    test_fraction: float = 0.0,
) -> dict[str, list[dict[str, Any]]]:
    """Chronological train/validation/test split (spec §113).

    Never random. The environment moves — the algorithm, the channel's
    audience, the operator's own prompt tweaks — so a model must be judged
    on videos published *after* the ones it learned from. A random split
    reports a number that cannot be reproduced by next week's uploads.
    """
    ordered = sorted(rows, key=lambda r: str(r.get("published_at") or ""))
    total = len(ordered)
    if total == 0:
        return {"train": [], "validation": [], "test": []}

    test_size = int(total * max(0.0, test_fraction))
    validation_size = int(total * max(0.0, validation_fraction))
    train_size = max(0, total - validation_size - test_size)
    return {
        "train": ordered[:train_size],
        "validation": ordered[train_size:train_size + validation_size],
        "test": ordered[train_size + validation_size:],
    }


def to_matrix(
    rows: Iterable[dict[str, Any]],
    target: str,
    *,
    mature_only: bool = True,
    feature_names: tuple[str, ...] = fr.FEATURE_NAMES,
) -> tuple[list[list[float]], list[float], list[dict[str, Any]]]:
    """Design matrix for one target: `(X, y, kept_rows)`.

    Rows without a value for this target are dropped, not zero-filled.
    Immature rows are dropped by default: a video measured six hours after
    upload has not finished happening, and training on it teaches the model
    that recent videos underperform.
    """
    features: list[list[float]] = []
    labels: list[float] = []
    kept: list[dict[str, Any]] = []
    for row in rows:
        if mature_only and not row.get("mature"):
            continue
        value = (row.get("targets") or {}).get(target)
        if value is None:
            continue
        row_features = row.get("features") or {}
        excluded = EXCLUDED_FEATURES
        features.append([
            0.0 if name in excluded else float(row_features.get(name, 0.0))
            for name in feature_names
        ])
        labels.append(transform_target(target, float(value)))
        kept.append(row)
    return features, labels, kept


def transform_target(target: str, value: float) -> float:
    """Map a raw label into the space the model is fitted in (spec §5)."""
    if target in LOG_TARGETS:
        import math

        return math.log1p(max(0.0, value))
    return value


def inverse_target(target: str, value: float) -> float:
    """Bring a model output back into real units.

    Log-space predictions are clamped at 0 first: `log1p` of a non-negative
    quantity is never negative, so anything below it is outside the domain
    the head was fitted on, and `expm1` would hand back a negative view
    multiple.
    """
    if target in LOG_TARGETS:
        import math

        return math.expm1(max(0.0, value))
    return value


def row_published_at(row: dict[str, Any]) -> _dt.datetime | None:
    return parse_date(row.get("published_at"))
