"""Typed records that flow through Brain.

Three families:

  - `VideoRecord` — a *published* video plus whatever ground truth we have.
    Built from `.mp/youtube.json`, so it tolerates every field being absent.
  - `Candidate` / `ChannelContext` — a *proposed* video and the channel it
    would be published on. This is all Brain is allowed to see at decision
    time; anything that only exists after publishing must not appear here
    (that constraint is what `features.registry` enforces mechanically).
  - `BrainPrediction` / `Explanation` / `DecisionRecord` — what Brain says,
    why, and what it later turned out to be worth.

Everything is a plain dataclass with `to_dict()` so records round-trip
through JSONL without a serializer library.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import re
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from .constants import SUCCESS_CLASSES

_VIDEO_ID_RE = re.compile(
    r"(?:youtu\.be/|youtube\.com/(?:watch\?v=|shorts/|video/|embed/))([\w-]{11})"
)
_DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%Y/%m/%d")


def new_id(prefix: str = "") -> str:
    raw = uuid.uuid4().hex[:12]
    return f"{prefix}_{raw}" if prefix else raw


def stable_id(*parts: Any) -> str:
    """Deterministic 12-hex id for a tuple of values (used for dedupe keys)."""
    payload = "|".join(str(p) for p in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def extract_video_id(url: str) -> str:
    match = _VIDEO_ID_RE.search(str(url or ""))
    return match.group(1) if match else ""


def parse_date(value: Any) -> _dt.datetime | None:
    """Parse the cache's date strings into an aware UTC datetime.

    The pipeline writes "%Y-%m-%d %H:%M:%S" (local, naive); RetentionSync
    writes ISO-8601 with a UTC offset. Naive values are read as UTC — an
    hours-level error is irrelevant for day-scale maturity math and keeps
    every comparison in the package on a single timeline.
    """
    if isinstance(value, _dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=_dt.timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=_dt.timezone.utc)
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return _dt.datetime.strptime(text, fmt).replace(tzinfo=_dt.timezone.utc)
        except ValueError:
            continue
    return None


def now_utc() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def to_number(value: Any, *, allow_negative: bool = False) -> float | None:
    """Cache-tolerant numeric read.

    The sync scripts write -1 for "not available", so a bare int() would
    silently teach the model that a video got minus one view.
    """
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    if not allow_negative and number < 0:
        return None
    return number


def success_class(normalized_views: float | None) -> str:
    """Bucket a channel-relative view multiple into a success label (§29)."""
    if normalized_views is None:
        return "unknown"
    label = SUCCESS_CLASSES[0][0]
    for name, floor in SUCCESS_CLASSES:
        if normalized_views >= floor:
            label = name
    return label


# -- Published videos ---------------------------------------------------


@dataclass
class VideoRecord:
    """One published video as Brain sees it."""

    account_id: str = ""
    video_id: str = ""
    url: str = ""
    title: str = ""
    description: str = ""
    topic: str = ""
    published_at: _dt.datetime | None = None
    is_short: bool = True
    duration_seconds: float | None = None
    thumbnail_path: str = ""
    retention_mode: str = ""
    # Pre-publish scores the existing labs already produce — Brain's first
    # honest features, because they were computed *before* the upload.
    retention_score: float | None = None
    hook_score: float | None = None
    first_image_score: float | None = None
    is_remix: bool = False
    # Ground truth.
    views: float | None = None
    likes: float | None = None
    comments: float | None = None
    dislikes: float | None = None
    stats_synced_at: _dt.datetime | None = None
    avg_percentage_viewed: float | None = None
    retention_curve: list[dict[str, float]] = field(default_factory=list)
    subscribers_gained: float | None = None
    impressions: float | None = None
    ctr: float | None = None
    production_cost: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_cache(cls, video: dict[str, Any], account_id: str = "") -> "VideoRecord":
        video = video or {}
        url = str(video.get("url") or "")
        duration = to_number(video.get("duration_seconds"))
        if duration is None:
            duration = to_number(video.get("duration"))
        return cls(
            account_id=str(account_id or ""),
            video_id=(
                extract_video_id(url)
                or stable_id(account_id, video.get("title"), video.get("date"))
            ),
            url=url,
            title=str(video.get("title") or ""),
            description=str(video.get("description") or ""),
            topic=str(video.get("subject") or video.get("topic") or ""),
            published_at=parse_date(video.get("date")),
            is_short=bool(video.get("is_short", True)),
            duration_seconds=duration,
            thumbnail_path=str(video.get("thumbnail_path") or ""),
            retention_mode=str(video.get("retention_mode") or ""),
            retention_score=to_number(video.get("retention_score")),
            hook_score=to_number(video.get("hook_score")),
            first_image_score=to_number(video.get("first_image_score")),
            is_remix=bool(video.get("is_remix", False)),
            views=to_number(video.get("view_count")),
            likes=to_number(video.get("like_count")),
            comments=to_number(video.get("comment_count")),
            dislikes=to_number(video.get("dislike_count")),
            stats_synced_at=parse_date(video.get("stats_synced_at")),
            avg_percentage_viewed=to_number(video.get("avg_percentage_viewed")),
            retention_curve=list(video.get("retention_curve") or []),
            subscribers_gained=to_number(video.get("subscribers_gained")),
            impressions=to_number(video.get("impressions")),
            ctr=to_number(video.get("ctr")),
            production_cost=to_number(video.get("production_cost")),
            raw=dict(video),
        )

    @property
    def is_published(self) -> bool:
        """A real, resolvable upload — not a placeholder cache entry."""
        return bool(self.video_id) and self.url not in ("", "uploading...")

    def age_days(self, now: _dt.datetime | None = None) -> float | None:
        if not self.published_at:
            return None
        delta = (now or now_utc()) - self.published_at
        return max(0.0, delta.total_seconds() / 86400.0)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("raw", None)
        for key in ("published_at", "stats_synced_at"):
            value = getattr(self, key)
            data[key] = value.isoformat() if value else None
        return data


# -- Candidates (pre-publish) -------------------------------------------


@dataclass
class Candidate:
    """A video Brain is asked to judge *before* anything is produced.

    Fields are optional by design: a topic-search candidate carries only a
    topic, while a pre-render candidate carries the script and thumbnail
    too. The feature layer reports which features it could actually fill,
    and confidence drops when the candidate is thin.
    """

    candidate_id: str = field(default_factory=lambda: new_id("cand"))
    topic: str = ""
    title: str = ""
    hook: str = ""
    script: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    kind: str = "short"  # "short" | "long"
    target_duration_seconds: float | None = None
    thumbnail_path: str = ""
    image_style: str = ""
    voice: str = ""
    retention_mode: str = ""
    planned_publish_at: _dt.datetime | None = None
    production_cost: float | None = None
    # Scores the existing labs already produced for this candidate, if any.
    retention_score: float | None = None
    hook_score: float | None = None
    first_image_score: float | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_topic(cls, topic: str, **kwargs: Any) -> "Candidate":
        return cls(topic=str(topic or "").strip(), **kwargs)

    @property
    def label(self) -> str:
        """Shortest human-readable identity for logs and tables."""
        return self.title or self.topic or self.hook or self.candidate_id

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["planned_publish_at"] = (
            self.planned_publish_at.isoformat() if self.planned_publish_at else None
        )
        return data


@dataclass
class ChannelContext:
    """The channel a candidate would be published on, plus its history."""

    account_id: str = ""
    nickname: str = ""
    niche: str = ""
    language: str = ""
    subscriber_count: float | None = None
    videos: list[VideoRecord] = field(default_factory=list)
    now: _dt.datetime = field(default_factory=now_utc)
    #: True for a channel imported only to learn from (someone else's).
    #: Never a generation or upload target.
    is_reference: bool = False

    @classmethod
    def from_account(
        cls, account: dict[str, Any], now: _dt.datetime | None = None
    ) -> "ChannelContext":
        account = account or {}
        account_id = str(account.get("id") or "")
        videos = [
            VideoRecord.from_cache(video, account_id)
            for video in (account.get("videos") or [])
        ]
        return cls(
            account_id=account_id,
            nickname=str(account.get("nickname") or ""),
            niche=str(account.get("niche") or ""),
            language=str(account.get("language") or ""),
            subscriber_count=to_number(account.get("subscriber_count")),
            videos=videos,
            now=now or now_utc(),
            is_reference=bool(account.get("reference", False)),
        )

    def published(self) -> list[VideoRecord]:
        return [v for v in self.videos if v.is_published]

    def before(self, moment: _dt.datetime) -> list[VideoRecord]:
        """History available *at* a moment — the anti-leakage primitive.

        Every channel-level feature is computed from this, so a row for a
        video published in March can never be influenced by an April hit.
        """
        return [
            v for v in self.published()
            if v.published_at and v.published_at < moment
        ]


# -- Predictions --------------------------------------------------------


@dataclass
class Factor:
    """One reason behind a score, in the score's own units."""

    name: str
    contribution: float
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Explanation:
    score: float = 0.0
    confidence: float = 0.0
    positive_factors: list[Factor] = field(default_factory=list)
    negative_factors: list[Factor] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 3),
            "confidence": round(self.confidence, 3),
            "positive_factors": [f.to_dict() for f in self.positive_factors],
            "negative_factors": [f.to_dict() for f in self.negative_factors],
            "notes": list(self.notes),
        }


@dataclass
class BrainPrediction:
    """Multi-head output for one candidate (spec §139)."""

    candidate_id: str = ""
    score: float = 0.0                 # 0-10 ranking score
    confidence: float = 0.0            # 0-1
    targets: dict[str, float] = field(default_factory=dict)
    success_probabilities: dict[str, float] = field(default_factory=dict)
    tier: str = ""
    model_version: str = ""
    source: str = ""                   # "heuristic" | "linear" | "hybrid"
    novelty: float | None = None
    explanation: Explanation | None = None

    def get(self, target: str) -> float | None:
        return self.targets.get(target)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "score": round(self.score, 3),
            "confidence": round(self.confidence, 3),
            "targets": {k: round(v, 5) for k, v in self.targets.items()},
            "success_probabilities": {
                k: round(v, 4) for k, v in self.success_probabilities.items()
            },
            "tier": self.tier,
            "model_version": self.model_version,
            "source": self.source,
            "novelty": None if self.novelty is None else round(self.novelty, 4),
            "explanation": self.explanation.to_dict() if self.explanation else None,
        }


@dataclass
class DecisionRecord:
    """What Brain saw, what it recommended, what actually happened (§13).

    Written at decision time with `actual_*` empty, then reconciled once the
    published video has real numbers. That pairing is what makes shadow mode
    (§152) measurable instead of a vibe.
    """

    decision_id: str = field(default_factory=lambda: new_id("dec"))
    created_at: str = field(default_factory=lambda: now_utc().isoformat())
    account_id: str = ""
    stage: str = "topic"               # topic | title | hook | script | thumbnail
    mode: str = ""
    model_version: str = ""
    tier: str = ""
    candidates: list[dict[str, Any]] = field(default_factory=list)
    brain_choice_id: str = ""
    brain_choice_label: str = ""
    predicted_score: float | None = None
    pipeline_choice_id: str = ""
    pipeline_choice_label: str = ""
    applied: bool = False              # did Brain actually change the outcome?
    video_id: str = ""                 # filled once the choice is published
    actual_score: float | None = None
    prediction_error: float | None = None
    reconciled_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
