"""Ridge regression, the rule table, and the model registry."""

from __future__ import annotations

import random

import pytest

from brain.models import heuristic
from brain.models import registry as model_registry
from brain.models.linear import RidgeModel, solve
from brain.models.multitask import MultiTaskModel


# -- linear algebra -----------------------------------------------------


def test_solve_recovers_a_known_system():
    solution = solve([[2.0, 1.0], [1.0, 3.0]], [5.0, 10.0])
    assert abs(solution[0] - 1.0) < 1e-9
    assert abs(solution[1] - 3.0) < 1e-9


def test_solve_rejects_a_singular_system():
    with pytest.raises(ValueError):
        solve([[1.0, 2.0], [2.0, 4.0]], [1.0, 2.0])


# -- ridge --------------------------------------------------------------


def _synthetic(rows: int = 200, seed: int = 11):
    generator = random.Random(seed)
    names = ["a", "b", "c"]
    features = [[generator.gauss(0, 1) for _ in names] for _ in range(rows)]
    labels = [
        3.0 * row[0] - 2.0 * row[1] + 0.5 + generator.gauss(0, 0.05) for row in features
    ]
    return features, labels, names


def test_ridge_recovers_the_sign_and_order_of_true_effects():
    features, labels, names = _synthetic()
    model = RidgeModel.fit(features, labels, names, alpha=0.5, target="t")
    weights = dict(model.importance())
    assert weights["a"] > 0 and weights["b"] < 0
    assert abs(weights["c"]) < 0.2
    assert abs(model.predict([1.0, 0.0, 0.0]) - 3.5) < 0.15


def test_contributions_sum_exactly_to_the_prediction():
    features, labels, names = _synthetic()
    model = RidgeModel.fit(features, labels, names, alpha=1.0)
    point = [0.7, -1.3, 2.0]
    total = sum(model.contributions(point).values()) + model.bias
    assert abs(total - model.predict(point)) < 1e-9


def test_ridge_survives_more_features_than_rows():
    """The regime this project starts in: 74 columns, a few dozen videos."""
    generator = random.Random(3)
    names = [f"f{i}" for i in range(40)]
    features = [[generator.gauss(0, 1) for _ in names] for _ in range(12)]
    labels = [generator.gauss(0, 1) for _ in range(12)]
    model = RidgeModel.fit(features, labels, names, alpha=1.0)
    assert len(model.weights) == 40
    assert all(value == value for value in model.weights)  # no NaNs


def test_constant_columns_get_no_influence():
    names = ["varying", "constant"]
    features = [[float(i), 5.0] for i in range(30)]
    labels = [2.0 * i for i in range(30)]
    model = RidgeModel.fit(features, labels, names, alpha=0.1)
    assert abs(dict(model.importance())["constant"]) < 1e-9


def test_ridge_round_trips_through_json():
    features, labels, names = _synthetic(rows=50)
    model = RidgeModel.fit(features, labels, names, alpha=2.0, target="views")
    restored = RidgeModel.from_dict(model.to_dict())
    point = [0.2, 0.4, -0.6]
    assert abs(restored.predict(point) - model.predict(point)) < 1e-6


# -- heuristic rules ----------------------------------------------------


def test_rules_do_not_fire_on_missing_features():
    features = {name: 0.0 for name in ("title_curiosity", "script_words_per_second")}
    with_all, factors_all = heuristic.score(features, missing=[])
    _, factors_none = heuristic.score(features, missing=features.keys())
    assert len(factors_none) <= len(factors_all)
    assert all(factor.name != "pacing" for factor in factors_none)


def test_heuristic_prefers_a_specific_headline_over_a_vague_one():
    vague = {"title_curiosity": 0.0, "title_specificity": 0.0, "title_chars": 12.0}
    sharp = {"title_curiosity": 2.0, "title_specificity": 3.0, "title_chars": 58.0}
    assert heuristic.score(sharp)[0] > heuristic.score(vague)[0]


def test_score_stays_inside_zero_to_ten():
    absurd = {name: 1e6 for name in (
        "title_curiosity", "title_specificity", "title_chars", "topic_novelty",
        "lab_retention_score", "lab_hook_score",
    )}
    score, _ = heuristic.score(absurd)
    assert 0.0 <= score <= 10.0


def test_shouting_titles_are_penalized():
    quiet = heuristic.score({"title_uppercase_ratio": 0.05})[0]
    shouting = heuristic.score({"title_uppercase_ratio": 0.9})[0]
    assert shouting < quiet


# -- registry -----------------------------------------------------------


def _model(version: str = "") -> MultiTaskModel:
    features, labels, names = _synthetic(rows=60)
    head = RidgeModel.fit(features, labels, names, alpha=1.0, target="normalized_views")
    return MultiTaskModel(
        version=version,
        feature_schema="hash-1",
        feature_names=names,
        heads={"normalized_views": head},
        calibration={"normalized_views": {"r2": 0.5, "n": 60, "residual_std": 0.4}},
        training={"samples": 60},
    )


def test_save_load_and_promote():
    version = model_registry.save(_model())
    assert version == "v0001"
    assert model_registry.champion() is None  # saving does not promote

    model_registry.promote(version)
    champion = model_registry.champion()
    assert champion is not None and champion.version == "v0001"


def test_versions_increment_and_rollback_returns_to_the_previous():
    first = model_registry.save(_model())
    second = model_registry.save(_model())
    assert (first, second) == ("v0001", "v0002")

    model_registry.promote(second)
    assert model_registry.rollback() == "v0001"
    assert model_registry.champion().version == "v0001"


def test_rollback_refuses_when_there_is_nowhere_to_go():
    model_registry.promote(model_registry.save(_model()))
    with pytest.raises(ValueError):
        model_registry.rollback()


def test_promoting_an_unknown_version_raises():
    with pytest.raises(FileNotFoundError):
        model_registry.promote("v9999")


def test_confidence_reflects_r2_and_sample_count():
    small = MultiTaskModel(calibration={"t": {"r2": 0.6, "n": 30}}, heads={})
    large = MultiTaskModel(calibration={"t": {"r2": 0.6, "n": 400}}, heads={})
    assert small.confidence("t") < large.confidence("t")
    assert MultiTaskModel().confidence("missing") == 0.0


def test_a_head_that_explains_nothing_reports_no_confidence():
    model = MultiTaskModel(calibration={"t": {"r2": -0.2, "n": 100}})
    assert model.confidence("t") == 0.0


# -- promotion gate -----------------------------------------------------


def test_first_model_is_promoted():
    promote, reasons = model_registry.promotion_decision(None, {"ranking": {"spearman": 0.1}})
    assert promote and "no champion" in reasons[0]


def test_a_better_fit_with_worse_ranking_is_refused():
    champion = {"ranking": {"spearman": 0.42}, "heads": {}}
    challenger = {"ranking": {"spearman": 0.30}, "heads": {}}
    promote, reasons = model_registry.promotion_decision(champion, challenger)
    assert not promote
    assert "below" in reasons[0]


def test_a_head_regression_blocks_promotion():
    champion = {
        "ranking": {"spearman": 0.30},
        "heads": {"retention": {"r2": 0.40}},
    }
    challenger = {
        "ranking": {"spearman": 0.50},
        "heads": {"retention": {"r2": 0.10}},
    }
    promote, reasons = model_registry.promotion_decision(champion, challenger)
    assert not promote
    assert any("regressed" in reason for reason in reasons)


def test_a_genuine_improvement_is_promoted():
    champion = {"ranking": {"spearman": 0.30}, "heads": {"retention": {"r2": 0.3}}}
    challenger = {"ranking": {"spearman": 0.55}, "heads": {"retention": {"r2": 0.31}}}
    promote, reasons = model_registry.promotion_decision(champion, challenger)
    assert promote
    assert "improved" in reasons[0]


def test_a_challenger_without_a_ranking_score_is_refused():
    promote, reasons = model_registry.promotion_decision(
        {"ranking": {"spearman": 0.2}}, {"ranking": {}}
    )
    assert not promote
