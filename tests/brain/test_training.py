"""Metrics, the training loop, and the walk-forward backtest.

The centrepiece is `test_training_discovers_the_planted_signal`: the fixture
channel's over-performers are exactly the videos whose titles carry a number
and a curiosity phrase. If the path from cache entry to fitted weights works,
the model finds that. If any link in the chain is broken, it cannot.
"""

from __future__ import annotations

from brain.config import get_reward_weights
from brain.data import dataset as ds
from brain.models import registry as model_registry
from brain.training import backtest as bt
from brain.training import evaluate as ev
from brain.training import train as trainer

from conftest import NOW, make_account, write_cache


# -- metrics ------------------------------------------------------------


def test_regression_metrics_on_a_perfect_fit():
    metrics = ev.regression_metrics([1.0, 2.0, 3.0], [1.0, 2.0, 3.0])
    assert metrics["mae"] == 0.0 and metrics["r2"] == 1.0


def test_r2_is_zero_when_predicting_the_mean():
    actual = [1.0, 2.0, 3.0, 4.0]
    metrics = ev.regression_metrics(actual, [2.5] * 4)
    assert abs(metrics["r2"]) < 1e-9


def test_spearman_handles_ties_and_perfect_order():
    assert ev.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == 1.0
    assert ev.spearman([1, 2, 3, 4], [40, 30, 20, 10]) == -1.0
    assert ev.spearman([1, 1, 1], [1, 2, 3]) is None
    assert ev.spearman([1, 2], [1, 2]) is None  # too few points


def test_ndcg_rewards_putting_the_best_first():
    good = ev.ndcg_at_k([10, 1, 1, 1], [9, 1, 1, 1], k=2)
    bad = ev.ndcg_at_k([10, 1, 1, 1], [1, 9, 9, 9], k=2)
    assert good > bad


def test_precision_at_k_counts_overlap():
    assert ev.precision_at_k([5, 4, 3, 2, 1], [5, 4, 3, 2, 1], k=3) == 1.0
    assert ev.precision_at_k([5, 4, 3, 2, 1], [1, 2, 3, 4, 5], k=2) == 0.0


# -- training -----------------------------------------------------------


def test_training_refuses_on_too_little_data(isolated_root):
    write_cache(isolated_root, [make_account(count=10)])
    from brain.data import collector

    rows = ds.build_rows(collector.load_channels(now=NOW), now=NOW)
    model, report = trainer.train(rows)
    assert model is None
    assert "below the" in report["reason"]
    assert report["trained"] is False


def test_training_discovers_the_planted_signal(dataset_rows):
    model, report = trainer.train(dataset_rows)
    assert model is not None, report.get("reason")
    assert "normalized_views" in model.heads
    assert "retention" in model.heads

    head_report = report["heads"]["normalized_views"]
    assert head_report["beats_mean_baseline"] is True
    assert head_report["selected_on"] == "validation"

    # The planted signal is the numbered/curiosity title shape.
    top_features = [name for name, _ in model.heads["normalized_views"].importance()[:12]]
    assert any(
        name in top_features
        for name in ("title_has_number", "title_curiosity", "title_specificity",
                     "topic_winner_similarity")
    ), top_features


def test_heads_are_absent_for_targets_with_no_data(dataset_rows):
    model, report = trainer.train(dataset_rows)
    assert "ctr" not in model.heads
    assert report["heads"]["ctr"]["skipped"] is True
    assert "mature labeled rows" in report["heads"]["ctr"]["reason"]


def test_ranking_evaluation_is_reported(dataset_rows):
    model, report = trainer.train(dataset_rows)
    ranking = report["evaluation"]["ranking"]
    assert ranking["n"] > 0
    assert ranking["spearman"] is not None


def test_model_predictions_beat_the_mean_on_held_out_rows(dataset_rows):
    model, _ = trainer.train(dataset_rows)
    holdout = ds.time_split(dataset_rows, validation_fraction=0.2)["validation"]
    features, labels, _ = ds.to_matrix(holdout, "normalized_views")

    model_error = ev.regression_metrics(
        labels, model.heads["normalized_views"].predict_many(features)
    )["rmse"]
    mean_error = ev.baseline_comparison(
        ds.time_split(dataset_rows, validation_fraction=0.2)["train"],
        holdout, "normalized_views")["rmse"]
    assert model_error < mean_error


def test_train_and_register_promotes_the_first_model(dataset_rows):
    result = trainer.train_and_register(dataset_rows)
    assert result["trained"] and result["promoted"]
    assert model_registry.champion().version == result["version"]

    manifest = model_registry.manifest_of(result["version"])
    assert manifest["dataset_hash"]
    assert manifest["samples"] == 60


def test_a_second_identical_model_is_not_promoted(dataset_rows):
    first = trainer.train_and_register(dataset_rows)
    second = trainer.train_and_register(dataset_rows)
    assert second["trained"]
    assert second["promoted"] is False
    assert model_registry.champion().version == first["version"]
    assert model_registry.get_pointer("challenger") == second["version"]


def test_dataset_hash_is_stable_and_content_sensitive(dataset_rows):
    assert trainer.dataset_hash(dataset_rows) == trainer.dataset_hash(dataset_rows)
    mutated = [dict(row) for row in dataset_rows]
    mutated[0] = dict(mutated[0], raw_views=999999)
    assert trainer.dataset_hash(mutated) != trainer.dataset_hash(dataset_rows)


def test_a_trained_model_predicts_in_real_units_and_raises_confidence(
    dataset_rows, channel
):
    from brain.inference.predictor import Predictor
    from brain.schema import Candidate

    candidate = Candidate(
        topic="9 datos que nadie te conto sobre el cometa Halley",
        planned_publish_at=NOW,
    )
    before = Predictor("acc-1", load_model=False).predict(candidate, channel)

    trainer.train_and_register(dataset_rows)
    predictor = Predictor("acc-1")
    assert predictor.model is not None
    assert predictor.tier == "basic_ml"

    after = predictor.predict(candidate, channel)
    assert after.source == "hybrid"
    # A view multiple is a non-negative multiple of the channel median.
    assert after.targets["normalized_views"] > 0
    assert 0.0 <= after.targets["retention"] <= 1.0
    # Having a measured model is worth more confidence than rules alone.
    assert after.confidence > before.confidence
    # But 60 videos buys modest confidence, not certainty. Deliberately not
    # asserted against the abstention floor: that is a configurable number,
    # and pinning a test to whichever side of it this fixture lands on makes
    # the suite fail every time a feature is added. The floor's behaviour is
    # covered by its own tests in test_inference.py.
    assert after.confidence < 0.7


def test_an_out_of_range_feature_cannot_wreck_a_prediction(dataset_rows, channel):
    """The synthetic channel posts every 2 days; ask about a 6-week gap.

    Standardization clips at 4 sigma, so a value hundreds of deviations out
    moves the score but cannot dominate it.
    """
    from brain.inference.predictor import Predictor
    from brain.schema import Candidate

    trainer.train_and_register(dataset_rows)
    predictor = Predictor("acc-1")
    prediction = predictor.predict(
        Candidate(topic="7 datos sobre el cometa Halley", planned_publish_at=NOW),
        channel,
    )
    assert 0.0 <= prediction.score <= 10.0
    assert prediction.targets["normalized_views"] >= 0.0


def test_success_probabilities_sum_to_one_when_calibrated(dataset_rows, channel):
    from brain.inference.predictor import Predictor
    from brain.schema import Candidate

    trainer.train_and_register(dataset_rows)
    prediction = Predictor("acc-1").predict(
        Candidate(topic="7 datos sobre el cometa", planned_publish_at=NOW), channel
    )
    if prediction.success_probabilities:
        assert abs(sum(prediction.success_probabilities.values()) - 1.0) < 1e-6


# -- backtest -----------------------------------------------------------


def test_backtest_needs_enough_history(isolated_root):
    result = bt.walk_forward(rows=[], warmup=40, batch_size=5)
    assert result["ran"] is False
    assert "warm-up" in result["reason"]


def test_walk_forward_simulates_decisions_and_reports_a_verdict(dataset_rows):
    result = bt.walk_forward(dataset_rows, warmup=40, batch_size=5)
    assert result["ran"] is True
    assert result["step_count"] >= 3
    assert -1.0 <= result["mean_lift_vs_mean"] <= 1.0
    assert 0.0 <= result["top1_accuracy"] <= 1.0
    assert result["verdict"]


def test_backtest_picks_the_strong_videos_on_a_planted_signal(dataset_rows):
    """With a signal this blunt, the simulated picks should beat the mean."""
    result = bt.walk_forward(dataset_rows, warmup=40, batch_size=5)
    assert result["mean_lift_vs_mean"] > 0


def test_head_readiness_counts_labeled_rows(dataset_rows):
    readiness = bt.head_readiness(dataset_rows)
    assert readiness["normalized_views"]["ready"] is True
    assert readiness["ctr"]["ready"] is False
    assert readiness["ctr"]["labeled_rows"] == 0


def test_reward_weights_are_normalized():
    weights = get_reward_weights("acc-1")
    assert abs(sum(weights.values()) - 1.0) < 1e-9
