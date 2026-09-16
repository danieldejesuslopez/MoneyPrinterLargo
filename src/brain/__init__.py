"""MoneyPrinter Brain — the learning layer over the generation pipeline.

Brain does not generate anything. It watches what the pipeline produces,
learns from what the audience did with it, and — once it has earned the
right — reorders the choices the pipeline was going to make anyway.

The public surface is deliberately tiny, and every entry point is safe to
call from inside a generation run:

    brain.status(account_id)                  what Brain currently knows
    brain.rank_topics(account_id, topics)     score a candidate set
    brain.suggest_topic(account_id, topics)   one pick, or "" if unsure
    brain.explain(prediction)                 why that score
    brain.observe()                           snapshot today's metrics

`suggest_topic` returns an empty string whenever Brain is disabled, is in a
non-deciding mode, or is not confident enough — so the caller's fallback is
its existing behaviour and integration is three lines with no new failure
mode (spec §63).

Layering, bottom up:

    data/       read the cache, snapshot metrics, build the dataset
    features/   candidate -> numbers, with a hard pre-publish boundary
    models/     the rule table, ridge heads, and the model registry
    inference/  predict, rank, explain
    training/   fit, evaluate, backtest, promote
    memory/     per-channel statistical profile
    experiments/the decision ledger that makes shadow mode measurable
"""

from __future__ import annotations

from typing import Any, Sequence

from . import config as brain_config
from .constants import BRAIN_VERSION, DECIDING_MODES, MODE_OFF, tier_for_samples
from .data import collector, dataset, validators
from .experiments import decisions
from .features import registry as features
from .inference.explainer import counterfactuals, explain_prediction, variant
from .inference.predictor import Predictor
from .inference.ranker import RankedCandidate, build_portfolio, rank, select, shortlist
from .memory import channel_memory
from .models import registry as model_registry
from .schema import BrainPrediction, Candidate, ChannelContext

__version__ = BRAIN_VERSION

__all__ = [
    "BrainPrediction",
    "Candidate",
    "ChannelContext",
    "Predictor",
    "RankedCandidate",
    "build_portfolio",
    "counterfactuals",
    "explain",
    "explain_prediction",
    "is_enabled",
    "load_channel",
    "mode",
    "observe",
    "rank",
    "rank_topics",
    "select",
    "shortlist",
    "status",
    "suggest_topic",
    "variant",
]


def is_enabled(account_id: str = "") -> bool:
    return brain_config.is_enabled(account_id)


def mode(account_id: str = "") -> str:
    return brain_config.get_mode(account_id)


def load_channel(account_id: str) -> ChannelContext | None:
    """The channel's history as Brain sees it."""
    return collector.find_channel(account_id)


def rank_topics(
    account_id: str,
    topics: Sequence[str],
    top_k: int = 10,
    stage: str = "topic",
    pipeline_choice: str = "",
    log: bool = True,
) -> dict[str, Any]:
    """Score and order a set of candidate topics for one channel.

    The workhorse entry point. Accepts plain strings — the pipeline already
    has them as strings — and returns a ranked table plus the reasoning for
    the top pick. Logs the decision so shadow mode can grade it later.
    """
    context = load_channel(account_id) or ChannelContext(account_id=account_id)
    candidates = [Candidate.from_topic(topic) for topic in topics if str(topic).strip()]
    if not candidates:
        return {"ranked": [], "top": None, "abstained": True,
                "reason": "no candidates supplied"}

    predictor = Predictor(account_id)
    ranked = shortlist(candidates, context, top_k=top_k, predictor=predictor)
    top = ranked[0] if ranked else None

    if log and mode(account_id) != MODE_OFF:
        decisions.record_ranking(
            account_id=account_id,
            stage=stage,
            ranked=ranked,
            mode=mode(account_id),
            pipeline_choice_label=pipeline_choice,
            applied=False,
            model_version=predictor.model_version,
            tier=predictor.tier,
        )

    confident = bool(top and predictor.should_decide(top.prediction))
    return {
        "ranked": [item.to_dict() for item in ranked],
        "top": top.candidate.label if top else None,
        "score": round(top.prediction.score, 2) if top else None,
        "confidence": round(top.prediction.confidence, 2) if top else None,
        "abstained": not confident,
        "tier": predictor.tier,
        "model_version": predictor.model_version,
        "why": explain_prediction(
            predictor.predict(top.candidate, context)
        ) if top else None,
    }


def suggest_topic(
    account_id: str,
    topics: Sequence[str],
    pipeline_choice: str = "",
) -> str:
    """Brain's pick, or "" when it should not be trusted with the choice.

    Empty is returned when Brain is disabled, when the mode is advisory or
    shadow, or when confidence is under the abstention floor (spec §76).
    The caller keeps its own selection in every one of those cases.
    """
    if not is_enabled(account_id) or mode(account_id) not in DECIDING_MODES:
        return ""
    try:
        result = rank_topics(account_id, topics, pipeline_choice=pipeline_choice)
    except Exception:
        # Brain is an optional layer. A failure here must never take down a
        # generation run that would otherwise have succeeded.
        return ""
    if result.get("abstained") or not result.get("top"):
        return ""
    return str(result["top"])


def explain(prediction: BrainPrediction) -> dict[str, Any]:
    return explain_prediction(prediction)


def observe(now=None) -> dict[str, Any]:
    """Record a metrics snapshot — Brain's only write during normal running.

    `brain.learn(result)` in the plan (spec §142) explicitly does *not*
    update weights inline. This is that function: it stores observations,
    and training happens in its own batch process.
    """
    return collector.record_snapshot(now=now)


def status(account_id: str = "") -> dict[str, Any]:
    """Brain health: model, data, readiness, and what is blocking progress."""
    from .inference.predictor import status as predictor_status

    rows = dataset.load_dataset()
    summary = dataset.summarize(rows)
    mature = summary.get("mature_rows", 0)
    channels = collector.load_channels()

    return {
        "brain_version": BRAIN_VERSION,
        "enabled": is_enabled(account_id),
        "mode": mode(account_id),
        "feature_schema": features.SCHEMA_HASH,
        "feature_count": features.FEATURE_COUNT,
        "predictor": predictor_status(account_id),
        "data": summary,
        "tier": tier_for_samples(mature),
        "channels": [
            {
                "account_id": channel.account_id,
                "nickname": channel.nickname,
                "published": len(channel.published()),
            }
            for channel in channels
        ],
        "registry": model_registry.registry_status(),
        "snapshots": collector.coverage_summary(),
        "shadow": decisions.shadow_report(account_id or None),
        "gaps": validators.data_gaps(channels),
    }


def refresh_memory() -> list[str]:
    """Rebuild every channel's statistical profile."""
    return channel_memory.refresh_profiles()


def briefing(account_id: str) -> str:
    """Prompt-ready facts about what works on this channel (spec §48)."""
    return channel_memory.briefing(account_id)
