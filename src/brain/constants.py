"""Stable names, versions and thresholds for MoneyPrinter Brain.

Everything here is a *contract*: feature names, target names, tier
boundaries and on-disk layout. Changing a value here invalidates trained
models, so `FEATURE_SCHEMA_VERSION` is bumped whenever the feature vector
changes shape and the registry refuses to load a model built against a
different schema.
"""

from __future__ import annotations

# ── Versions ───────────────────────────────────────────────────────────

BRAIN_VERSION = "0.1.0"
#: Bump whenever the feature vector's names/order/meaning change.
FEATURE_SCHEMA_VERSION = 4
#: Bump whenever a stored dataset row's shape changes.
DATASET_SCHEMA_VERSION = 1

# ── Autopilot modes (spec §58) ─────────────────────────────────────────

MODE_OFF = "off"                # Brain records nothing but snapshots.
MODE_SHADOW = "shadow"          # Brain predicts and logs; never influences.
MODE_ADVISORY = "advisory"      # Brain recommends; the caller may ignore it.
MODE_ASSISTED = "assisted"      # Brain filters/reranks candidate sets.
MODE_AUTO = "auto"              # Brain picks, above the confidence floor.
MODE_AGGRESSIVE = "aggressive"  # Brain picks and runs its own experiments.

MODES = (MODE_OFF, MODE_SHADOW, MODE_ADVISORY, MODE_ASSISTED, MODE_AUTO, MODE_AGGRESSIVE)

#: Modes in which Brain is allowed to change what the pipeline does.
DECIDING_MODES = (MODE_ASSISTED, MODE_AUTO, MODE_AGGRESSIVE)

# ── Data tiers (spec §86) ──────────────────────────────────────────────
# Which model family the amount of *labeled* data can honestly support.

TIER_HEURISTIC = "heuristic"        # < 50 labeled videos
TIER_BASIC_ML = "basic_ml"          # 50 - 199
TIER_NEURAL_RANKER = "neural_ranker"  # 200 - 999
TIER_MULTITASK = "multitask"        # 1000+

TIER_THRESHOLDS = (
    (1000, TIER_MULTITASK),
    (200, TIER_NEURAL_RANKER),
    (50, TIER_BASIC_ML),
    (0, TIER_HEURISTIC),
)

# ── Prediction targets (spec §26) ──────────────────────────────────────
# Every target the multi-head model can learn. A target is only trained
# when enough rows carry a real label for it; the rest stay heuristic.

TARGET_NORMALIZED_VIEWS = "normalized_views"
TARGET_RETENTION = "retention"
TARGET_WATCH_MINUTES = "watch_minutes"
TARGET_LIKE_RATE = "like_rate"
TARGET_COMMENT_RATE = "comment_rate"
TARGET_CTR = "ctr"
TARGET_SUBSCRIBER_RATE = "subscriber_rate"

TARGETS = (
    TARGET_NORMALIZED_VIEWS,
    TARGET_RETENTION,
    TARGET_WATCH_MINUTES,
    TARGET_LIKE_RATE,
    TARGET_COMMENT_RATE,
    TARGET_CTR,
    TARGET_SUBSCRIBER_RATE,
)

#: Targets trained in log space (heavy right tail: a 40x video is not
#: "40 units better" than a 1x one, it is one order of magnitude better).
LOG_TARGETS = (TARGET_NORMALIZED_VIEWS, TARGET_WATCH_MINUTES)

#: Minimum labeled rows before a target gets its own regression head.
MIN_SAMPLES_PER_TARGET = 30

# ── Reward weights (spec §94) ──────────────────────────────────────────

DEFAULT_REWARD_WEIGHTS = {
    TARGET_NORMALIZED_VIEWS: 0.25,
    TARGET_RETENTION: 0.25,
    TARGET_WATCH_MINUTES: 0.20,
    TARGET_LIKE_RATE: 0.05,
    TARGET_COMMENT_RATE: 0.05,
    TARGET_SUBSCRIBER_RATE: 0.10,
    TARGET_CTR: 0.10,
}

# ── Hybrid scoring weights (spec §137) ─────────────────────────────────

DEFAULT_HYBRID_WEIGHTS = {
    "neural": 0.65,
    "heuristic": 0.20,
    "rules": 0.10,
    "novelty": 0.05,
}

# ── Label maturity (spec §111, §112) ───────────────────────────────────

#: Metric snapshots older than this many days are the "settled" label.
LABEL_MATURE_DAYS = 7
#: Snapshot ages (hours) we keep as separate label windows.
LABEL_WINDOWS_HOURS = (1, 6, 24, 48, 168, 720, 2160)

# ── Success classes (spec §29) ─────────────────────────────────────────
# Bucket boundaries on normalized_views (1.0 == channel median).

SUCCESS_CLASSES = (
    ("failed", 0.0),
    ("weak", 0.35),
    ("normal", 0.75),
    ("good", 1.25),
    ("breakout", 2.0),
    ("viral", 4.0),
)

# ── On-disk layout ─────────────────────────────────────────────────────

BRAIN_CACHE_DIRNAME = "brain"          # under .mp/
MODELS_DIRNAME = "brain"               # under models/
CURRENT_MODEL_LINK = "current"
CHALLENGER_MODEL_LINK = "challenger"

METRICS_HISTORY_FILE = "metrics_history.jsonl"
DECISIONS_FILE = "decisions.jsonl"
DATASET_FILE = "dataset.jsonl"
EMBEDDINGS_DIRNAME = "embeddings"

# ── Guardrails ─────────────────────────────────────────────────────────

#: Below this confidence Brain abstains rather than deciding (spec §76).
DEFAULT_ABSTAIN_CONFIDENCE = 0.45
#: Two candidates closer than this (0-1 similarity) are near-duplicates.
DEFAULT_DIVERSITY_THRESHOLD = 0.72


def tier_for_samples(sample_count: int) -> str:
    """Strongest model family this much labeled data can honestly support."""
    for floor, tier in TIER_THRESHOLDS:
        if sample_count >= floor:
            return tier
    return TIER_HEURISTIC
