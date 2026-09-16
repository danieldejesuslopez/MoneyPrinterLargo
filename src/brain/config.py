"""Brain configuration: safe defaults, config.json overrides, on-disk paths.

The rest of the package must keep working when `config.json` is missing,
malformed, or has no `brain` section at all — Brain is an *addition* to the
pipeline, never a new way for it to crash. Every getter therefore falls back
to a documented default instead of raising.

Config shape (all optional), under the top-level `"brain"` key:

    "brain": {
        "enabled": true,
        "mode": "shadow",
        "abstain_confidence": 0.45,
        "diversity_threshold": 0.72,
        "reward_weights": {"retention": 0.3, ...},
        "hybrid_weights": {"neural": 0.65, ...},
        "thresholds": {"topic_min_score": 7.5, ...},
        "channels": {
            "<account_uuid>": {"mode": "advisory", "reward_weights": {...}}
        }
    }

Per-channel entries override the global ones key by key (spec §95), so a
channel that lives on watch-time can weight it heavily without touching the
rest of the system.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .constants import (
    DEFAULT_ABSTAIN_CONFIDENCE,
    DEFAULT_DIVERSITY_THRESHOLD,
    DEFAULT_HYBRID_WEIGHTS,
    DEFAULT_REWARD_WEIGHTS,
    BRAIN_CACHE_DIRNAME,
    MODELS_DIRNAME,
    MODE_SHADOW,
    MODES,
)

#: Score gates before a candidate may proceed (spec §109). These start as
#: fixed numbers and are meant to be re-learned from history (spec §110).
DEFAULT_THRESHOLDS = {
    "topic_min_score": 6.5,
    "script_min_score": 7.0,
    "thumbnail_min_score": 6.5,
    "overall_min_score": 7.0,
}


def root_dir() -> str:
    """Project root, derived from this file, not from `sys.path`.

    `config.ROOT_DIR` is computed as `dirname(sys.path[0])`, which is right
    for `python src/main.py` but wrong under pytest or any other entry
    point. Brain resolves its own root so tests and the CLI agree.
    """
    return str(Path(__file__).resolve().parents[2])


def config_path() -> str:
    return os.path.join(root_dir(), "config.json")


def read_config() -> dict[str, Any]:
    """Whole config.json, or {} when absent/unreadable/not an object."""
    try:
        with open(config_path(), "r", encoding="utf-8") as file:
            parsed = json.load(file)
    except (OSError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def brain_config(account_id: str | None = None) -> dict[str, Any]:
    """Merged Brain settings: defaults <- global <- per-channel."""
    section = read_config().get("brain")
    section = section if isinstance(section, dict) else {}

    merged: dict[str, Any] = {
        "enabled": True,
        "mode": MODE_SHADOW,
        "abstain_confidence": DEFAULT_ABSTAIN_CONFIDENCE,
        "diversity_threshold": DEFAULT_DIVERSITY_THRESHOLD,
        "reward_weights": dict(DEFAULT_REWARD_WEIGHTS),
        "hybrid_weights": dict(DEFAULT_HYBRID_WEIGHTS),
        "thresholds": dict(DEFAULT_THRESHOLDS),
    }
    _merge_into(merged, section)

    if account_id:
        channels = section.get("channels")
        if isinstance(channels, dict):
            override = channels.get(str(account_id))
            if isinstance(override, dict):
                _merge_into(merged, override)
    return merged


def _merge_into(target: dict[str, Any], source: dict[str, Any]) -> None:
    """One-level-deep merge: dict values merge key-by-key, others replace."""
    for key, value in (source or {}).items():
        if key == "channels":
            continue
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            target[key] = {**target[key], **value}
        elif value is not None:
            target[key] = value


def is_enabled(account_id: str | None = None) -> bool:
    """Master switch. `MP_BRAIN_ENABLED` in the env wins over config.json."""
    env = os.environ.get("MP_BRAIN_ENABLED", "").strip().lower()
    if env:
        return env in {"1", "true", "yes", "on"}
    return bool(brain_config(account_id).get("enabled", True))


def get_mode(account_id: str | None = None) -> str:
    """Autopilot mode, clamped to a known mode (`MP_BRAIN_MODE` wins)."""
    value = os.environ.get("MP_BRAIN_MODE", "").strip().lower()
    if not value:
        value = str(brain_config(account_id).get("mode") or MODE_SHADOW).strip().lower()
    return value if value in MODES else MODE_SHADOW


def get_reward_weights(account_id: str | None = None) -> dict[str, float]:
    """Per-channel reward weights, normalized to sum to 1 (spec §95)."""
    raw = brain_config(account_id).get("reward_weights") or {}
    weights: dict[str, float] = {}
    for key, value in raw.items():
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            weights[str(key)] = number
    if not weights:
        weights = dict(DEFAULT_REWARD_WEIGHTS)
    total = sum(weights.values()) or 1.0
    return {key: value / total for key, value in weights.items()}


def get_hybrid_weights(account_id: str | None = None) -> dict[str, float]:
    """Blend weights for neural / heuristic / rules / novelty (spec §137)."""
    raw = brain_config(account_id).get("hybrid_weights") or {}
    weights = dict(DEFAULT_HYBRID_WEIGHTS)
    for key, value in raw.items():
        if key not in weights:
            continue
        try:
            weights[key] = max(0.0, float(value))
        except (TypeError, ValueError):
            continue
    total = sum(weights.values()) or 1.0
    return {key: value / total for key, value in weights.items()}


def get_thresholds(account_id: str | None = None) -> dict[str, float]:
    raw = brain_config(account_id).get("thresholds") or {}
    thresholds = dict(DEFAULT_THRESHOLDS)
    for key, value in raw.items():
        try:
            thresholds[str(key)] = float(value)
        except (TypeError, ValueError):
            continue
    return thresholds


def get_abstain_confidence(account_id: str | None = None) -> float:
    try:
        value = float(brain_config(account_id).get("abstain_confidence"))
    except (TypeError, ValueError):
        return DEFAULT_ABSTAIN_CONFIDENCE
    return min(1.0, max(0.0, value))


def get_diversity_threshold(account_id: str | None = None) -> float:
    try:
        value = float(brain_config(account_id).get("diversity_threshold"))
    except (TypeError, ValueError):
        return DEFAULT_DIVERSITY_THRESHOLD
    return min(1.0, max(0.0, value))


# ── Paths ──────────────────────────────────────────────────────────────


def cache_dir() -> str:
    """`.mp/brain/` — snapshots, datasets, decision log, embeddings."""
    path = os.path.join(root_dir(), ".mp", BRAIN_CACHE_DIRNAME)
    os.makedirs(path, exist_ok=True)
    return path


def cache_file(name: str) -> str:
    return os.path.join(cache_dir(), name)


def models_dir() -> str:
    """`models/brain/` — versioned model directories plus `current`."""
    path = os.path.join(root_dir(), "models", MODELS_DIRNAME)
    os.makedirs(path, exist_ok=True)
    return path


def youtube_cache_path() -> str:
    """`.mp/youtube.json` — the channel history Brain learns from."""
    return os.path.join(root_dir(), ".mp", "youtube.json")
