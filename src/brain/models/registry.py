"""Versioned model storage, champion/challenger, and promotion gates.

Layout under `models/brain/`:

    v0001/model.json      the model itself
    v0001/manifest.json   dataset hash, feature schema, metrics, timestamp
    current.json          {"version": "v0001"}  <- what inference loads
    challenger.json       {"version": "v0002"}  <- what is being evaluated

Pointer files rather than symlinks, because symlinks on Windows need
elevation and this project runs there.

The promotion gate (spec §72) is the important part: a new model is *never*
promoted just for having a lower training loss. It has to beat the champion
on held-out ranking quality, keep its calibration, and not regress any head
by more than a small tolerance. A model that ranks worse but fits better is
exactly the failure this project cannot afford, because ranking is the only
thing Brain is actually used for.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
from typing import Any

from ..config import models_dir
from ..constants import CHALLENGER_MODEL_LINK, CURRENT_MODEL_LINK
from .multitask import MultiTaskModel

_VERSION_RE = re.compile(r"^v(\d{4,})$")

#: A challenger must beat the champion's ranking correlation by at least
#: this much to be promoted. Small enough to allow real progress, large
#: enough that noise on a few dozen validation rows cannot trigger it.
MIN_RANKING_GAIN = 0.02
#: No individual head may lose more than this much R^2.
MAX_HEAD_REGRESSION = 0.05


def version_dir(version: str) -> str:
    return os.path.join(models_dir(), version)


def list_versions() -> list[str]:
    """Known model versions, oldest first."""
    root = models_dir()
    versions = [
        name for name in os.listdir(root)
        if _VERSION_RE.match(name) and os.path.isdir(os.path.join(root, name))
    ]
    versions.sort()
    return versions


def next_version() -> str:
    existing = list_versions()
    if not existing:
        return "v0001"
    highest = max(int(_VERSION_RE.match(name).group(1)) for name in existing)
    return f"v{highest + 1:04d}"


def save(
    model: MultiTaskModel,
    manifest: dict[str, Any] | None = None,
    version: str | None = None,
) -> str:
    """Write a model plus its manifest. Returns the version it was stored as."""
    target_version = version or next_version()
    model.version = target_version
    if not model.created_at:
        model.created_at = _dt.datetime.now(_dt.timezone.utc).isoformat()

    directory = version_dir(target_version)
    os.makedirs(directory, exist_ok=True)
    _write_json(os.path.join(directory, "model.json"), model.to_dict())
    _write_json(
        os.path.join(directory, "manifest.json"),
        {
            "version": target_version,
            "created_at": model.created_at,
            "brain_version": model.brain_version,
            "feature_schema": model.feature_schema,
            "tier": model.tier,
            "targets": model.targets,
            "calibration": model.head_metrics(),
            **(manifest or {}),
        },
    )
    return target_version


def load(version: str) -> MultiTaskModel | None:
    path = os.path.join(version_dir(version), "model.json")
    data = _read_json(path)
    return MultiTaskModel.from_dict(data) if data else None


def manifest_of(version: str) -> dict[str, Any]:
    return _read_json(os.path.join(version_dir(version), "manifest.json")) or {}


# -- Pointers -----------------------------------------------------------


def _pointer_path(name: str) -> str:
    return os.path.join(models_dir(), f"{name}.json")


def set_pointer(name: str, version: str) -> None:
    _write_json(
        _pointer_path(name),
        {"version": version, "updated_at": _dt.datetime.now(_dt.timezone.utc).isoformat()},
    )


def get_pointer(name: str) -> str:
    data = _read_json(_pointer_path(name)) or {}
    return str(data.get("version") or "")


def clear_pointer(name: str) -> None:
    path = _pointer_path(name)
    if os.path.exists(path):
        os.remove(path)


def champion() -> MultiTaskModel | None:
    """The model inference should use, or None while Brain is heuristic-only."""
    version = get_pointer(CURRENT_MODEL_LINK)
    return load(version) if version else None


def challenger() -> MultiTaskModel | None:
    version = get_pointer(CHALLENGER_MODEL_LINK)
    return load(version) if version else None


def promote(version: str) -> None:
    """Make `version` the champion (spec §72)."""
    if not os.path.exists(version_dir(version)):
        raise FileNotFoundError(f"unknown model version: {version}")
    set_pointer(CURRENT_MODEL_LINK, version)
    if get_pointer(CHALLENGER_MODEL_LINK) == version:
        clear_pointer(CHALLENGER_MODEL_LINK)


def rollback() -> str:
    """Return to the previous version (spec §128). Returns the new champion."""
    versions = list_versions()
    current = get_pointer(CURRENT_MODEL_LINK)
    if current in versions:
        index = versions.index(current)
        if index == 0:
            raise ValueError("no earlier model version to roll back to")
        previous = versions[index - 1]
    elif versions:
        previous = versions[-1]
    else:
        raise ValueError("no model versions on disk")
    set_pointer(CURRENT_MODEL_LINK, previous)
    return previous


# -- Promotion gate -----------------------------------------------------


def promotion_decision(
    champion_metrics: dict[str, Any] | None,
    challenger_metrics: dict[str, Any],
) -> tuple[bool, list[str]]:
    """Should the challenger replace the champion? Returns `(promote, why)`.

    Ranking quality decides, because ranking is what Brain does: pick the
    best candidate out of a set. A model can have a worse RMSE and still be
    the better chooser, and vice versa (spec §74).
    """
    reasons: list[str] = []

    if not champion_metrics:
        reasons.append("no champion yet - first trained model is promoted")
        return True, reasons

    champion_rank = _ranking_metric(champion_metrics)
    challenger_rank = _ranking_metric(challenger_metrics)
    if challenger_rank is None:
        reasons.append("challenger has no ranking score - refusing to promote")
        return False, reasons
    if champion_rank is None:
        reasons.append("champion had no ranking score; challenger measured one")
        return True, reasons

    gain = challenger_rank - champion_rank
    if gain < MIN_RANKING_GAIN:
        reasons.append(
            f"ranking correlation gain {gain:+.3f} is below the "
            f"{MIN_RANKING_GAIN:+.3f} required"
        )
        return False, reasons
    reasons.append(f"ranking correlation improved {gain:+.3f}")

    regressions = _head_regressions(champion_metrics, challenger_metrics)
    if regressions:
        reasons.extend(regressions)
        return False, reasons

    return True, reasons


def _ranking_metric(metrics: dict[str, Any]) -> float | None:
    value = (metrics or {}).get("ranking", {}).get("spearman")
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _head_regressions(
    champion_metrics: dict[str, Any], challenger_metrics: dict[str, Any]
) -> list[str]:
    """Heads that got materially worse — each one blocks promotion."""
    problems: list[str] = []
    old_heads = (champion_metrics or {}).get("heads") or {}
    new_heads = (challenger_metrics or {}).get("heads") or {}
    for target, old in old_heads.items():
        new = new_heads.get(target)
        if not isinstance(old, dict):
            continue
        old_r2 = float(old.get("r2", 0.0) or 0.0)
        if new is None:
            if old_r2 > 0:
                problems.append(f"head '{target}' disappeared (was R2={old_r2:.3f})")
            continue
        new_r2 = float(new.get("r2", 0.0) or 0.0)
        if old_r2 - new_r2 > MAX_HEAD_REGRESSION:
            problems.append(
                f"head '{target}' regressed R2 {old_r2:.3f} -> {new_r2:.3f}"
            )
    return problems


# -- IO helpers ---------------------------------------------------------


def _write_json(path: str, data: dict[str, Any]) -> None:
    """Atomic write, matching how the rest of the project persists state."""
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def _read_json(path: str) -> dict[str, Any] | None:
    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def registry_status() -> dict[str, Any]:
    versions = list_versions()
    current = get_pointer(CURRENT_MODEL_LINK)
    return {
        "versions": versions,
        "champion": current,
        "challenger": get_pointer(CHALLENGER_MODEL_LINK),
        "champion_manifest": manifest_of(current) if current else {},
    }
