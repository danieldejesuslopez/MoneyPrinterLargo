"""Ranking a candidate set: score, de-duplicate, diversify, explore.

Picking the single highest score every time is the fastest way to make a
channel boring: the model's favourite pattern gets repeated until the
audience stops responding, and — worse — the system stops generating the
varied data it needs to keep learning. So selection has three stages beyond
scoring (spec §34, §57):

  - **Diversity.** Near-duplicate candidates collapse to their best member,
    so a shortlist of ten is ten different ideas rather than one idea in ten
    phrasings.
  - **Exploration.** A configurable share of picks comes from strong-but-not
    -top candidates, which is how a fixed policy discovers that its beliefs
    were wrong.
  - **Abstention.** Below the confidence floor the ranking is advisory only
    and the caller keeps its own choice (spec §76).

The exploration draw takes an injected `random.Random`, so tests are
deterministic and a run can be reproduced from its seed.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..config import get_diversity_threshold
from ..features import lexicon as lx
from ..schema import BrainPrediction, Candidate, ChannelContext
from .predictor import Predictor

#: Share of selections drawn from the exploration pool rather than the top.
DEFAULT_EXPLORATION_RATE = 0.2
#: Exploration only ever considers candidates within this much of the best,
#: so "explore" never means "publish something we think is bad".
EXPLORATION_SCORE_MARGIN = 2.0


@dataclass
class RankedCandidate:
    candidate: Candidate
    prediction: BrainPrediction
    rank: int = 0
    duplicate_of: str = ""
    selected_by: str = ""

    @property
    def score(self) -> float:
        return self.prediction.score

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate.candidate_id,
            "label": self.candidate.label,
            "rank": self.rank,
            "score": round(self.prediction.score, 3),
            "confidence": round(self.prediction.confidence, 3),
            "novelty": (
                None if self.prediction.novelty is None
                else round(self.prediction.novelty, 3)
            ),
            "duplicate_of": self.duplicate_of,
            "selected_by": self.selected_by,
        }


def rank(
    candidates: Sequence[Candidate],
    context: ChannelContext,
    predictor: Predictor | None = None,
    explain: bool = False,
) -> list[RankedCandidate]:
    """Score every candidate and order them best-first."""
    if not candidates:
        return []
    engine = predictor or Predictor(context.account_id)
    predictions = engine.predict_many(list(candidates), context, explain=explain)

    ranked = [
        RankedCandidate(candidate=candidate, prediction=prediction)
        for candidate, prediction in zip(candidates, predictions)
    ]
    ranked.sort(key=lambda item: item.score, reverse=True)
    for position, item in enumerate(ranked, start=1):
        item.rank = position
    return ranked


def _candidate_tokens(candidate: Candidate) -> set[str]:
    return lx.content_tokens(f"{candidate.topic} {candidate.title} {candidate.hook}")


def deduplicate(
    ranked: Sequence[RankedCandidate],
    threshold: float | None = None,
    account_id: str = "",
) -> list[RankedCandidate]:
    """Collapse near-identical candidates, keeping the best-scoring one.

    Complements the pipeline's existing duplicate rejection, which compares
    a candidate against *published history*; this one compares candidates
    against *each other* inside a single generation batch.
    """
    limit = threshold if threshold is not None else get_diversity_threshold(account_id)
    kept: list[RankedCandidate] = []
    kept_tokens: list[tuple[str, set[str]]] = []

    for item in ranked:
        tokens = _candidate_tokens(item.candidate)
        duplicate_of = ""
        for other_id, other_tokens in kept_tokens:
            if tokens and lx.jaccard(tokens, other_tokens) >= limit:
                duplicate_of = other_id
                break
        if duplicate_of:
            item.duplicate_of = duplicate_of
            continue
        kept.append(item)
        kept_tokens.append((item.candidate.candidate_id, tokens))
    return kept


def shortlist(
    candidates: Sequence[Candidate],
    context: ChannelContext,
    top_k: int = 10,
    predictor: Predictor | None = None,
    threshold: float | None = None,
) -> list[RankedCandidate]:
    """Rank, drop near-duplicates, and keep the best `top_k` (spec §31)."""
    engine = predictor or Predictor(context.account_id)
    ranked = rank(candidates, context, engine)
    unique = deduplicate(ranked, threshold, context.account_id)
    return unique[: max(1, top_k)]


def select(
    ranked: Sequence[RankedCandidate],
    exploration_rate: float = DEFAULT_EXPLORATION_RATE,
    rng: random.Random | None = None,
) -> RankedCandidate | None:
    """Choose one candidate: mostly the best, sometimes a nearby alternative.

    The exploration pool is bounded by `EXPLORATION_SCORE_MARGIN` below the
    top score, so exploring costs a little expected performance and never
    ships something the model actively dislikes.
    """
    if not ranked:
        return None
    generator = rng or random.Random()
    best = ranked[0]

    if len(ranked) == 1 or generator.random() >= max(0.0, min(1.0, exploration_rate)):
        best.selected_by = "exploitation"
        return best

    pool = [
        item for item in ranked[1:]
        if best.score - item.score <= EXPLORATION_SCORE_MARGIN
    ]
    if not pool:
        best.selected_by = "exploitation"
        return best
    chosen = generator.choice(pool)
    chosen.selected_by = "exploration"
    return chosen


def rank_table(ranked: Sequence[RankedCandidate], limit: int = 10) -> list[dict[str, Any]]:
    """Compact rows for a CLI table or an API response."""
    return [item.to_dict() for item in ranked[:limit]]


@dataclass
class Portfolio:
    """A week's worth of picks, spread deliberately (spec §56, §57)."""

    picks: list[RankedCandidate] = field(default_factory=list)
    exploration_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "picks": [item.to_dict() for item in self.picks],
            "exploration_count": self.exploration_count,
        }


def build_portfolio(
    ranked: Sequence[RankedCandidate],
    size: int = 7,
    exploration_rate: float = DEFAULT_EXPLORATION_RATE,
    rng: random.Random | None = None,
) -> Portfolio:
    """Pick `size` diverse candidates, reserving a share for exploration.

    Choosing a batch is a different problem from choosing one video: the
    right batch is not the top `n` scores, because those are correlated
    bets. Exploration slots are filled from further down the ranking so a
    bad week cannot be bad in only one way.
    """
    generator = rng or random.Random()
    remaining = list(ranked)
    portfolio = Portfolio()
    explore_slots = int(round(size * max(0.0, min(1.0, exploration_rate))))

    for index in range(min(size, len(remaining))):
        exploring = index >= (size - explore_slots) and len(remaining) > 1
        if exploring:
            pool = remaining[1:] or remaining
            choice = generator.choice(pool)
            choice.selected_by = "exploration"
            portfolio.exploration_count += 1
        else:
            choice = remaining[0]
            choice.selected_by = "exploitation"
        remaining.remove(choice)
        portfolio.picks.append(choice)
    return portfolio
