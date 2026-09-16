"""Dataset validation, leakage detection, and the data-gap report.

Three jobs:

  - `validate_row` / `validate_dataset` — structural sanity before training.
  - `leakage_report` — a *functional* check that features cannot see results.
    The registry already refuses to vectorize post-publish specs; this goes
    further and proves it empirically by rewriting a video's results and
    confirming its feature vector does not move (spec §148).
  - `data_gaps` — what Brain needs that the pipeline does not record yet.
    This is the honest counterpart to the plan's ambition: several of the
    headline targets (CTR, subscribers gained, impressions) simply have no
    source in this repo today, and a model cannot learn a column that does
    not exist.
"""

from __future__ import annotations

from typing import Any

from ..constants import (
    MIN_SAMPLES_PER_TARGET,
    TARGET_CTR,
    TARGET_RETENTION,
    TARGET_SUBSCRIBER_RATE,
    TARGETS,
)
from ..features import registry as fr
from ..features.base import POST_PUBLISH
from ..schema import ChannelContext, VideoRecord
from . import collector
from . import dataset as ds


def validate_row(row: dict[str, Any]) -> list[str]:
    """Structural problems with one dataset row (empty list == fine)."""
    problems: list[str] = []
    if not row.get("video_id"):
        problems.append("missing video_id")
    if not row.get("published_at"):
        problems.append("missing published_at")

    features = row.get("features")
    if not isinstance(features, dict):
        problems.append("features is not an object")
    else:
        missing = [name for name in fr.FEATURE_NAMES if name not in features]
        if missing:
            problems.append(f"{len(missing)} feature(s) absent from the row")
        unknown = [name for name in features if name not in fr.SPEC_BY_NAME]
        if unknown:
            problems.append(f"{len(unknown)} unknown feature(s): {unknown[:3]}")

    if row.get("feature_schema") and row["feature_schema"] != fr.SCHEMA_HASH:
        problems.append("row was built against a different feature schema")

    targets = row.get("targets")
    if not isinstance(targets, dict):
        problems.append("targets is not an object")
    else:
        for name, value in targets.items():
            if name not in TARGETS:
                problems.append(f"unknown target {name}")
            elif value is not None and not isinstance(value, (int, float)):
                problems.append(f"target {name} is not numeric")
    return problems


def validate_dataset(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate validation plus the duplicate check (spec §111)."""
    problems: dict[str, list[str]] = {}
    seen: set[str] = set()
    duplicates: list[str] = []
    stale_schema = 0

    for row in rows:
        row_problems = validate_row(row)
        if row_problems:
            problems[str(row.get("video_id") or "?")] = row_problems
        video_id = str(row.get("video_id") or "")
        if video_id:
            if video_id in seen:
                duplicates.append(video_id)
            seen.add(video_id)
        if row.get("feature_schema") and row["feature_schema"] != fr.SCHEMA_HASH:
            stale_schema += 1

    trainable = {
        target: sum(
            1 for row in rows
            if row.get("mature") and (row.get("targets") or {}).get(target) is not None
        )
        for target in TARGETS
    }
    return {
        "rows": len(rows),
        "invalid_rows": len(problems),
        "problems": problems,
        "duplicate_video_ids": duplicates,
        "stale_schema_rows": stale_schema,
        "trainable_per_target": trainable,
        "targets_ready": [
            target for target, count in trainable.items()
            if count >= MIN_SAMPLES_PER_TARGET
        ],
    }


def leakage_report(sample_rows: int = 25) -> dict[str, Any]:
    """Prove that results cannot reach the model input (spec §114, §148).

    Structural half: no post-publish spec is in the input set. Functional
    half: take real videos, multiply every result metric, rebuild the
    feature vector and confirm nothing changed. If a feature ever reads
    `views`, this fails loudly instead of quietly inflating a backtest.
    """
    structural = [
        spec.name for spec in fr.INPUT_SPECS if spec.availability == POST_PUBLISH
    ]

    changed: list[str] = []
    checked = 0
    channels = collector.load_channels()
    for channel in channels:
        for record in channel.published()[:sample_rows]:
            if not record.published_at:
                continue
            checked += 1
            before = fr.vectorize(ds.candidate_from_record(record), channel).values
            after = fr.vectorize(
                ds.candidate_from_record(_with_inflated_results(record)), channel
            ).values
            if before != after:
                changed.append(record.video_id)
            if checked >= sample_rows:
                break
        if checked >= sample_rows:
            break

    return {
        "post_publish_features_in_input": structural,
        "records_checked": checked,
        "records_whose_features_moved": changed,
        "clean": not structural and not changed,
    }


def _with_inflated_results(record: VideoRecord) -> VideoRecord:
    """Copy of a record with every *result* metric changed, inputs intact."""
    import copy

    mutated = copy.copy(record)
    mutated.views = (record.views or 100.0) * 37.0
    mutated.likes = (record.likes or 10.0) * 37.0
    mutated.comments = (record.comments or 5.0) * 37.0
    mutated.avg_percentage_viewed = 99.0
    mutated.subscribers_gained = 1234.0
    mutated.ctr = 42.0
    mutated.raw = dict(record.raw)
    mutated.raw.update(
        {"view_count": mutated.views, "like_count": mutated.likes,
         "comment_count": mutated.comments}
    )
    return mutated


# -- Data gaps ----------------------------------------------------------

#: Each gap: what is missing, what it blocks, and where it would come from.
_GAP_DEFINITIONS = (
    (
        "duration_seconds",
        "Watch-time labels and any duration-vs-retention learning",
        "Write the rendered clip's duration into the cache entry at upload time.",
    ),
    (
        "script",
        "Every script-structure feature (17 of them) on historical videos",
        "Persist the final narration alongside the cache entry, or in the "
        "<video>.meta.json sidecar the reupload path already reads.",
    ),
    (
        "ctr",
        "The CTR head, and therefore title/thumbnail ranking on real data",
        "Scrape impressions and CTR from Studio the way RetentionSync already "
        "scrapes average-percentage-viewed.",
    ),
    (
        "subscribers_gained",
        "Subscriber-conversion head",
        "Same Studio page as CTR.",
    ),
    (
        "production_cost",
        "Cost-efficiency scoring and the quality-per-dollar trade-off",
        "Record the per-video generation cost the Higgsfield cost module "
        "already computes.",
    ),
)


def data_gaps(channels: list[ChannelContext] | None = None) -> dict[str, Any]:
    """What Brain needs, what it has, and what to start recording.

    Coverage is measured over published videos, so the answer is about this
    installation's real history rather than about the schema in the
    abstract.
    """
    channels = channels if channels is not None else collector.load_channels()
    videos = [video for channel in channels for video in channel.published()]
    total = len(videos)

    def coverage(attribute: str) -> float:
        if not total:
            return 0.0
        present = sum(
            1 for video in videos
            if getattr(video, attribute, None) not in (None, "", [])
        )
        return present / total

    def raw_coverage(key: str) -> float:
        if not total:
            return 0.0
        present = sum(1 for video in videos if (video.raw or {}).get(key))
        return present / total

    gaps = []
    for field_name, blocks, remedy in _GAP_DEFINITIONS:
        if field_name == "script":
            have = raw_coverage("script")
        else:
            have = coverage(field_name)
        gaps.append({
            "field": field_name,
            "coverage": round(have, 3),
            "blocks": blocks,
            "remedy": remedy,
        })

    snapshots = collector.coverage_summary()
    return {
        "published_videos": total,
        "gaps": sorted(gaps, key=lambda g: g["coverage"]),
        "retention_coverage": round(coverage("avg_percentage_viewed"), 3),
        "snapshot_history": snapshots,
        "notes": _gap_notes(total, coverage, snapshots),
    }


def _gap_notes(total: int, coverage, snapshots: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    if not total:
        notes.append(
            "No published videos in .mp/youtube.json yet - Brain has nothing to "
            "learn from and will stay in heuristic mode."
        )
        return notes
    if coverage("avg_percentage_viewed") < 0.3:
        notes.append(
            f"Only {coverage('avg_percentage_viewed'):.0%} of videos have real "
            "retention. Run the Studio retention sync to unlock the "
            f"{TARGET_RETENTION} head."
        )
    if coverage("ctr") == 0:
        notes.append(
            f"No CTR data anywhere, so the {TARGET_CTR} head cannot be trained "
            "and title/thumbnail ranking stays heuristic."
        )
    if coverage("subscribers_gained") == 0:
        notes.append(
            f"No subscriber attribution, so the {TARGET_SUBSCRIBER_RATE} head "
            "cannot be trained."
        )
    if not snapshots.get("snapshots"):
        notes.append(
            "No metric snapshots recorded yet. Velocity labels (views_1h, "
            "views_24h) only start existing once `brain snapshot` runs "
            "regularly - historical velocity cannot be recovered."
        )
    return notes
