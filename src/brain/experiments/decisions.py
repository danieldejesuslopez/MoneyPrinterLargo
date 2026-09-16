"""The decision log: what Brain recommended, and what it was worth.

Shadow mode (spec §151, §152) is mandatory before Brain is allowed to decide
anything, and it only means something if both halves are recorded: the
recommendation *and* the outcome of whatever actually shipped. This module
owns that ledger.

Flow:

  1. At decision time, `record_ranking` appends a `DecisionRecord` — the
     whole candidate set with scores, Brain's pick, the pipeline's pick, and
     whether Brain's pick was actually applied.
  2. Later, `reconcile` matches recorded picks against published videos and
     fills in the realized reward and the prediction error.
  3. `shadow_report` turns the ledger into the only claim worth making:
     over N decisions, did Brain's preference correlate with what worked?

Deliberately append-only JSONL. A decision is a historical fact; rewriting
one to look better is exactly the failure mode this file exists to prevent.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from typing import Any, Sequence

from ..config import cache_file, get_reward_weights
from ..constants import DECISIONS_FILE
from ..data import collector
from ..data import normalizer as norm
from ..features import lexicon as lx
from ..schema import DecisionRecord, now_utc

#: Similarity above which a logged pick is considered the same idea as a
#: published video. High, because a wrong match invents a result.
MATCH_THRESHOLD = 0.6


def decisions_path() -> str:
    return cache_file(DECISIONS_FILE)


def log_decision(record: DecisionRecord) -> DecisionRecord:
    """Append one decision. Never raises — logging must not break a run."""
    try:
        with open(decisions_path(), "a", encoding="utf-8") as file:
            file.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
    except OSError:
        pass
    return record


def record_ranking(
    account_id: str,
    stage: str,
    ranked: Sequence[Any],
    mode: str,
    pipeline_choice_label: str = "",
    applied: bool = False,
    model_version: str = "",
    tier: str = "",
) -> DecisionRecord:
    """Log a ranking run. `ranked` is a list of `RankedCandidate`."""
    top = ranked[0] if ranked else None
    record = DecisionRecord(
        account_id=account_id,
        stage=stage,
        mode=mode,
        model_version=model_version or (
            top.prediction.model_version if top else ""
        ),
        tier=tier or (top.prediction.tier if top else ""),
        candidates=[item.to_dict() for item in ranked[:20]],
        brain_choice_id=top.candidate.candidate_id if top else "",
        brain_choice_label=top.candidate.label if top else "",
        predicted_score=top.prediction.score if top else None,
        pipeline_choice_label=pipeline_choice_label,
        applied=applied,
    )
    return log_decision(record)


def load_decisions(account_id: str | None = None) -> list[dict[str, Any]]:
    path = decisions_path()
    if not os.path.exists(path):
        return []
    rows: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            if account_id and row.get("account_id") != account_id:
                continue
            rows.append(row)
    return rows


def _rewrite(rows: list[dict[str, Any]]) -> None:
    """Rewrite the ledger in place — only ever used to fill in outcomes."""
    path = decisions_path()
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(tmp_path, path)


def _best_match(label: str, videos: list[Any]) -> Any | None:
    """The published video that best matches a logged candidate label."""
    tokens = lx.content_tokens(label)
    if not tokens:
        return None
    best = None
    best_score = 0.0
    for video in videos:
        score = lx.jaccard(tokens, lx.content_tokens(f"{video.title} {video.topic}"))
        if score > best_score:
            best, best_score = video, score
    return best if best_score >= MATCH_THRESHOLD else None


def reconcile(account_id: str | None = None) -> dict[str, Any]:
    """Fill in what actually happened for decisions whose pick was published.

    Only decisions where Brain's pick *was applied* can be scored against a
    real outcome. For the rest the ledger keeps the prediction and leaves
    the result empty — an unshipped recommendation has no result, and
    pretending otherwise is how shadow-mode reports start lying.
    """
    rows = load_decisions()
    if not rows:
        return {"decisions": 0, "reconciled": 0}

    channels = {c.account_id: c for c in collector.load_channels()}
    reconciled = 0

    for row in rows:
        if row.get("reconciled_at") or not row.get("applied"):
            continue
        if account_id and row.get("account_id") != account_id:
            continue
        channel = channels.get(str(row.get("account_id") or ""))
        if not channel:
            continue

        published = [
            video for video in channel.published()
            if video.published_at
            and video.published_at >= (_parse(row.get("created_at")) or video.published_at)
        ]
        match = _best_match(str(row.get("brain_choice_label") or ""), published)
        if not match:
            continue

        weights = get_reward_weights(channel.account_id)
        peers = norm.peer_window(match, channel.published())
        labels = norm.build_labels(match, peers)
        actual = norm.reward(labels["targets"], weights)
        if actual is None:
            continue

        row["video_id"] = match.video_id
        row["actual_score"] = round(10.0 * actual, 3)
        predicted = row.get("predicted_score")
        if predicted is not None:
            row["prediction_error"] = round(row["actual_score"] - float(predicted), 3)
        row["reconciled_at"] = now_utc().isoformat()
        reconciled += 1

    if reconciled:
        _rewrite(rows)
    return {"decisions": len(rows), "reconciled": reconciled}


def _parse(value: Any) -> _dt.datetime | None:
    from ..schema import parse_date

    return parse_date(value)


def shadow_report(account_id: str | None = None) -> dict[str, Any]:
    """Has Brain been right? (spec §153)

    Reports separately on decisions Brain influenced and decisions where it
    only watched, because only the first kind has an outcome attached.
    """
    rows = load_decisions(account_id)
    if not rows:
        return {
            "decisions": 0,
            "message": "No decisions logged yet. Brain records one every time "
                       "it ranks a candidate set.",
        }

    scored = [row for row in rows if row.get("actual_score") is not None]
    agreements = [
        row for row in rows
        if row.get("pipeline_choice_label")
        and row.get("brain_choice_label")
    ]
    agreed = sum(
        1 for row in agreements
        if lx.normalize(str(row["brain_choice_label"]))
        == lx.normalize(str(row["pipeline_choice_label"]))
    )

    errors = [abs(float(row["prediction_error"])) for row in scored
              if row.get("prediction_error") is not None]

    by_stage: dict[str, int] = {}
    for row in rows:
        stage = str(row.get("stage") or "unknown")
        by_stage[stage] = by_stage.get(stage, 0) + 1

    return {
        "decisions": len(rows),
        "by_stage": by_stage,
        "applied": sum(1 for row in rows if row.get("applied")),
        "reconciled": len(scored),
        "agreement_rate": (agreed / len(agreements)) if agreements else None,
        "mean_absolute_error": (sum(errors) / len(errors)) if errors else None,
        "message": _shadow_message(len(rows), len(scored), errors),
    }


def _shadow_message(total: int, scored: int, errors: list[float]) -> str:
    if scored == 0:
        return (
            f"{total} decisions logged, none with a measured outcome yet. "
            "Outcomes appear once a recommended video is published and its "
            "stats are synced."
        )
    if scored < 10:
        return (
            f"{scored} of {total} decisions have outcomes - too few to judge "
            "Brain's accuracy. Keep it in shadow mode."
        )
    mean_error = sum(errors) / len(errors) if errors else 0.0
    return (
        f"{scored} decisions have outcomes; mean absolute score error "
        f"{mean_error:.2f} on a 0-10 scale."
    )
