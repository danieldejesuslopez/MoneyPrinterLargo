"""Predictor blending, abstention, ranking, diversity and explanation."""

from __future__ import annotations

import random

from brain.inference import ranker
from brain.inference.explainer import counterfactuals, explain_prediction, variant
from brain.inference.predictor import Predictor
from brain.schema import Candidate, ChannelContext

from conftest import NOW

SHARP = "7 datos que nadie te conto sobre el cometa Halley"
VAGUE = "Cosas del espacio"


def _candidates(*titles: str) -> list[Candidate]:
    return [Candidate(topic=title, planned_publish_at=NOW) for title in titles]


# -- predictor ----------------------------------------------------------


def test_scores_a_candidate_with_no_model_at_all():
    prediction = Predictor("acc-1").predict(
        Candidate(topic=SHARP), ChannelContext(account_id="acc-1")
    )
    assert prediction.source == "heuristic"
    assert prediction.model_version == "heuristic"
    assert prediction.tier == "heuristic"
    assert 0.0 <= prediction.score <= 10.0


def test_a_sharper_topic_outscores_a_vague_one(channel):
    predictor = Predictor("acc-1")
    sharp, vague = predictor.predict_many(_candidates(SHARP, VAGUE), channel)
    assert sharp.score > vague.score


def test_confidence_is_capped_without_a_model(channel):
    prediction = Predictor("acc-1").predict(Candidate(topic=SHARP), channel)
    assert prediction.confidence <= 0.5


def test_confidence_rises_with_candidate_completeness(channel):
    predictor = Predictor("acc-1")
    thin = predictor.predict(Candidate(topic=SHARP, planned_publish_at=NOW), channel)
    full = predictor.predict(
        Candidate(
            topic=SHARP,
            title=SHARP,
            hook="El cometa Halley volvera en 2061",
            script="En 1986 el cometa Halley paso cerca. Pero algo no encajaba. "
                   "Los astronomos midieron 15 kilometros de nucleo.",
            target_duration_seconds=45.0,
            retention_score=8.2,
            hook_score=8.0,
            planned_publish_at=NOW,
        ),
        channel,
    )
    assert full.confidence > thin.confidence


def test_brain_abstains_below_the_confidence_floor(channel):
    predictor = Predictor("acc-1")
    prediction = predictor.predict(Candidate(topic=SHARP), channel)
    assert predictor.should_decide(prediction) is False


def test_an_incompatible_model_is_ignored_with_a_note(channel):
    from brain.models import registry as model_registry
    from brain.models.multitask import MultiTaskModel

    stale = MultiTaskModel(feature_schema="not-the-current-hash")
    model_registry.promote(model_registry.save(stale))

    predictor = Predictor("acc-1")
    assert predictor.model is None
    assert any("feature schema" in note for note in predictor.notes)


def test_lab_scores_participate_in_the_blend(channel):
    predictor = Predictor("acc-1")
    plain = Candidate(topic=SHARP, planned_publish_at=NOW)
    graded = Candidate(
        topic=SHARP, retention_score=9.5, hook_score=9.5, first_image_score=9.5,
        planned_publish_at=NOW,
    )
    assert predictor.predict(graded, channel).score > predictor.predict(plain, channel).score


# -- ranking ------------------------------------------------------------


def test_rank_orders_best_first_and_numbers_the_ranks(channel):
    ranked = ranker.rank(_candidates(VAGUE, SHARP), channel)
    assert [item.rank for item in ranked] == [1, 2]
    assert ranked[0].candidate.topic == SHARP


def test_deduplicate_collapses_restatements_of_one_idea(channel):
    candidates = _candidates(
        "El misterio del cometa Halley que nadie explico",
        "El misterio del cometa Halley que nadie ha explicado",
        "La musica barroca del siglo XVII en Europa",
    )
    ranked = ranker.rank(candidates, channel)
    unique = ranker.deduplicate(ranked, threshold=0.6)
    assert len(unique) == 2
    assert any(item.duplicate_of for item in ranked)


def test_shortlist_limits_and_deduplicates(channel):
    ranked = ranker.shortlist(_candidates(SHARP, VAGUE, "Otro tema distinto"), channel, top_k=2)
    assert len(ranked) == 2


def test_selection_is_deterministic_under_a_seed(channel):
    ranked = ranker.rank(_candidates(SHARP, VAGUE, "Un tercer tema"), channel)
    first = ranker.select(ranked, exploration_rate=0.5, rng=random.Random(4))
    second = ranker.select(ranked, exploration_rate=0.5, rng=random.Random(4))
    assert first.candidate.candidate_id == second.candidate.candidate_id


def test_zero_exploration_always_takes_the_top(channel):
    ranked = ranker.rank(_candidates(SHARP, VAGUE), channel)
    chosen = ranker.select(ranked, exploration_rate=0.0, rng=random.Random(1))
    assert chosen.rank == 1
    assert chosen.selected_by == "exploitation"


def test_exploration_never_picks_far_below_the_best(channel):
    ranked = ranker.rank(_candidates(SHARP, VAGUE), channel)
    for seed in range(20):
        chosen = ranker.select(ranked, exploration_rate=1.0, rng=random.Random(seed))
        gap = ranked[0].score - chosen.score
        assert gap <= ranker.EXPLORATION_SCORE_MARGIN


def test_portfolio_reserves_exploration_slots(channel):
    ranked = ranker.rank(_candidates(*[f"Tema numero {i}" for i in range(10)]), channel)
    portfolio = ranker.build_portfolio(ranked, size=6, exploration_rate=0.34,
                                       rng=random.Random(2))
    assert len(portfolio.picks) == 6
    assert portfolio.exploration_count == 2
    assert len({item.candidate.candidate_id for item in portfolio.picks}) == 6


def test_ranking_an_empty_set_is_safe(channel):
    assert ranker.rank([], channel) == []
    assert ranker.select([]) is None


# -- explanation --------------------------------------------------------


def test_explanation_lists_readable_factors(channel):
    prediction = Predictor("acc-1").predict(Candidate(topic=SHARP), channel)
    payload = explain_prediction(prediction)
    assert payload["positive"]
    assert all(item["factor"] for item in payload["positive"])
    assert "components" in " ".join(payload["notes"])


def test_counterfactuals_rank_the_change_that_helps_most(channel):
    predictor = Predictor("acc-1")
    base = Candidate(topic=VAGUE, planned_publish_at=NOW)
    result = counterfactuals(
        predictor, base, channel,
        {
            "sharper topic": variant(base, topic=SHARP),
            "same again": variant(base, topic=VAGUE),
        },
    )
    assert result["best"]["label"] == "sharper topic"
    assert result["variants"][0]["delta"] > 0
