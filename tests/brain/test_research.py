"""The research loop: what Claude is allowed to change, and how answers are judged.

No test here talks to Claude. The advisor is an injected callable, so every
test hands the loop a scripted answer — including hostile ones — and checks
that only valid, untried, in-range experiments are ever run, that a round
never changes configuration on its own, and that adoption is explicit.
"""

from __future__ import annotations

import json

import pytest

from brain.data import dataset as ds
from brain.data import normalizer as norm
from brain.research import advisor, bench, knobs, ledger, loop
from brain.training import train as trainer

from conftest import NOW


# -- fast settings ----------------------------------------------------------


@pytest.fixture
def quick_training(monkeypatch):
    """Two alphas, no blending: the walk-forward retrains many times."""
    monkeypatch.setattr(trainer, "ALPHA_GRID", (1.0, 16.0))
    monkeypatch.setattr(trainer, "ALPHA_BLEND", 1)


def write_config(root, data):
    (root / "config.json").write_text(json.dumps(data), encoding="utf-8")


# -- knobs --------------------------------------------------------------------


def test_validation_rejects_everything_outside_the_menu():
    clean, errors = knobs.validate({
        "peer_window": 8,
        "label_winsor": 0.9,
        "rm_rf": True,
        "alpha_blend": 2.5,
        "exclude_feature_groups": ["thumbnail", "made_up"],
    })
    assert clean == {"peer_window": 8}
    joined = " ".join(errors)
    assert "label_winsor" in joined and "above the maximum" in joined
    assert "unknown knob 'rm_rf'" in joined
    assert "expected an integer" in joined
    assert "made_up" in joined


def test_validation_rejects_a_non_object():
    clean, errors = knobs.validate(["peer_window", 8])
    assert clean == {} and errors


def test_excluding_nearly_every_group_is_refused():
    groups = knobs.all_groups()
    _, errors = knobs.validate({"exclude_feature_groups": groups})
    assert errors


def test_applied_sets_and_restores_module_constants():
    before = trainer.LABEL_WINSOR
    with knobs.applied({"label_winsor": 0.15}):
        assert trainer.LABEL_WINSOR == 0.15
    assert trainer.LABEL_WINSOR == before


def test_applied_restores_even_when_the_block_raises():
    before = norm.PEER_WINDOW
    with pytest.raises(RuntimeError):
        with knobs.applied({"peer_window": 9}):
            raise RuntimeError("boom")
    assert norm.PEER_WINDOW == before
    assert knobs._depth == 0


def test_applied_refuses_invalid_changes_before_touching_anything():
    before = trainer.ALPHA_BLEND
    with pytest.raises(ValueError):
        with knobs.applied({"alpha_blend": 99}):
            pass
    assert trainer.ALPHA_BLEND == before


def test_approved_overrides_come_from_config_json(isolated_root):
    write_config(isolated_root, {"brain": {"training": {"label_winsor": 0.1,
                                                        "nonsense": 1}}})
    overrides, errors = knobs.approved_overrides()
    assert overrides == {"label_winsor": 0.1}
    assert errors


def test_configured_does_not_override_a_running_experiment(isolated_root):
    write_config(isolated_root, {"brain": {"training": {"label_winsor": 0.1}}})
    with knobs.configured():
        assert trainer.LABEL_WINSOR == 0.1
    with knobs.applied({"label_winsor": 0.02}):
        with knobs.configured():
            assert trainer.LABEL_WINSOR == 0.02


def test_peer_window_knob_actually_changes_the_labels(channel):
    """The bug that motivated call-time constants: a bound default ignored it."""
    with knobs.applied({"peer_window": 4}):
        narrow = ds.build_rows([channel], now=NOW)
    with knobs.applied({"peer_window": 40}):
        wide = ds.build_rows([channel], now=NOW)
    narrow_labels = [r["targets"]["normalized_views"] for r in narrow]
    wide_labels = [r["targets"]["normalized_views"] for r in wide]
    assert narrow_labels != wide_labels


def test_excluded_feature_groups_get_exactly_zero_weight(dataset_rows, quick_training):
    with knobs.applied({"exclude_feature_groups": ["thumbnail", "temporal"]}):
        model, _ = trainer.train(dataset_rows, targets=("normalized_views",))
    head = model.heads["normalized_views"]
    excluded = knobs.features_in_groups(["thumbnail", "temporal"])
    for name, weight in zip(head.feature_names, head.weights):
        if name in excluded:
            assert weight == 0.0, name
    assert any(abs(w) > 0 for n, w in zip(head.feature_names, head.weights)
               if n not in excluded)


# -- advisor --------------------------------------------------------------------


def test_parse_response_handles_fences_prose_and_garbage():
    fenced = 'Claro:\n```json\n{"diagnosis": "x", "experiments": []}\n```\nsaludos'
    assert advisor.parse_response(fenced)["diagnosis"] == "x"
    prose = 'Aqui va: {"experiments": [{"name": "a"}]} fin'
    assert advisor.parse_response(prose)["experiments"][0]["name"] == "a"
    assert advisor.parse_response("no json at all") == {}
    assert advisor.parse_response("") == {}


def test_extract_proposals_keeps_only_safe_untried_experiments(isolated_root):
    parsed = {
        "diagnosis": "Falta retencion.",
        "experiments": [
            {"name": "Local Window!", "hypothesis": "h", "changes": {"peer_window": 8}},
            {"name": "evil", "changes": {"__import__": "os"}},
            {"name": "too_big", "changes": {"label_winsor": 5}},
            {"name": "same_again", "changes": {"peer_window": 8}},
            {"name": "old_idea", "changes": {"alpha_blend": 1}},
            {"name": "noop", "changes": {}},
            {"name": "second", "changes": {"label_winsor": 0.1}},
            {"name": "third", "changes": {"alpha_blend": 5}},
            "not even an object",
        ],
        "feature_ideas": [{"name": "Retencion Real", "rationale": "r",
                           "data_needed": "Studio"}],
        "data_priorities": ["retencion"],
    }
    tried = {ledger.canonical({"alpha_blend": 1})}
    out = advisor.extract_proposals(parsed, max_experiments=2, tried=tried)

    names = [e["name"] for e in out["experiments"]]
    assert names == ["local_window", "second"]
    reasons = {r.get("name"): r["reason"] for r in out["rejected"]}
    assert "unknown knob" in reasons["evil"]
    assert "above the maximum" in reasons["too_big"]
    assert "duplicate" in reasons["same_again"]
    assert "already tried" in reasons["old_idea"]
    assert "no changes" in reasons["noop"]
    assert "over the limit" in reasons["third"]
    assert out["feature_ideas"][0]["name"] == "retencion_real"


def test_a_proposal_identical_to_the_current_config_is_not_run(isolated_root):
    write_config(isolated_root, {"brain": {"training": {"peer_window": 12}}})
    parsed = {"experiments": [{"name": "same", "changes": {"peer_window": 12}}]}
    out = advisor.extract_proposals(parsed, 4, tried=set())
    assert out["experiments"] == []
    assert "identical" in out["rejected"][0]["reason"]


def test_prompt_carries_the_rules_and_the_evidence():
    brief = {
        "evaluation_protocol": {"min_search_gain_pp": 1.0, "min_confirm_probability": 0.8,
                                "confirmation_era_experiments_used": 3,
                                "confirmation_era_budget": 12},
        "knobs": [{"name": "peer_window"}],
        "history": [{"name": "tried_before"}],
    }
    prompt = advisor.build_prompt(brief, 2)
    assert "at most 2 experiments" in prompt
    assert "3 of a" in prompt and "12" in prompt
    assert "tried_before" in prompt
    assert "Never repeat" in prompt


def test_system_prompt_is_safe_to_pass_to_a_windows_cmd_shim():
    for forbidden in '"%^&|<>':
        assert forbidden not in advisor.SYSTEM_PROMPT


def test_cli_invocation_disables_every_tool(monkeypatch):
    captured = {}

    class Done:
        returncode = 0
        stdout = '{"experiments": []}'
        stderr = ""

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["input"] = kwargs.get("input")
        return Done()

    monkeypatch.setattr(advisor.subprocess, "run", fake_run)
    assert advisor.ask_claude("hola") == '{"experiments": []}'
    args = captured["args"]
    assert args[args.index("--tools") + 1] == ""
    assert args[args.index("--permission-mode") + 1] == "dontAsk"
    assert "--no-session-persistence" in args
    assert captured["input"] == "hola"


def test_cli_failures_become_readable_errors(monkeypatch):
    class Failed:
        returncode = 1
        stdout = ""
        stderr = "not logged in"

    monkeypatch.setattr(advisor.subprocess, "run", lambda *a, **k: Failed())
    with pytest.raises(RuntimeError, match="not logged in"):
        advisor.ask_claude("hola")


# -- bench ------------------------------------------------------------------------


def _evaluation(video_ids, actual, predicted, null=None, format_null=None):
    return bench.Evaluation(
        changes={},
        video_ids=list(video_ids),
        actual=list(actual),
        predicted=list(predicted),
        null=list(null if null is not None else [0.5] * len(actual)),
        format_null=list(format_null if format_null is not None else [0.5] * len(actual)),
        kinds=["short"] * len(actual),
    )


def test_gain_is_relative_to_the_null_model():
    assert bench.gain([1.0, 2.0], [1.0, 2.0], [0.0, 0.0]) == 100.0
    assert bench.gain([1.0, 2.0], [0.0, 0.0], [0.0, 0.0]) == 0.0
    assert bench.gain([], [], []) is None


def test_eras_are_aligned_by_video_and_split_in_time():
    ids = [f"v{i}" for i in range(12)]
    current = _evaluation(ids, [0.5] * 12, [0.5] * 12)
    candidate = _evaluation(ids[1:], [0.5] * 11, [0.5] * 11)   # one video missing
    search, confirm = bench.aligned_eras(current, candidate)
    assert len(search) + len(confirm) == 11
    assert all(current.video_ids[c] == candidate.video_ids[k] for c, k in search + confirm)
    assert max(c for c, _ in search) < min(c for c, _ in confirm)


def test_a_uniformly_better_candidate_is_recommended():
    ids = [f"v{i}" for i in range(30)]
    actual = [(i % 5) / 4 for i in range(30)]
    current = _evaluation(ids, actual, [0.5] * 30)
    candidate = _evaluation(ids, actual, [a * 0.8 + 0.1 for a in actual])
    verdict = bench.compare(current, candidate)
    assert verdict["verdict"] == "recommended"
    assert verdict["confirm"]["probability_better"] >= 0.8


def test_a_candidate_that_only_wins_the_search_era_is_rejected():
    ids = [f"v{i}" for i in range(30)]
    actual = [(i % 5) / 4 for i in range(30)]
    current = _evaluation(ids, actual, [a * 0.7 + 0.15 for a in actual])
    # good on the first 20 (search), noise on the last 10 (confirmation)
    candidate_pred = [a if i < 20 else 1.0 - a for i, a in enumerate(actual)]
    candidate = _evaluation(ids, actual, candidate_pred)
    verdict = bench.compare(current, candidate)
    assert verdict["verdict"] == "rejected_confirm"


def test_a_candidate_no_better_on_search_is_rejected_there():
    ids = [f"v{i}" for i in range(30)]
    actual = [(i % 5) / 4 for i in range(30)]
    same = [a * 0.7 + 0.15 for a in actual]
    verdict = bench.compare(_evaluation(ids, actual, same), _evaluation(ids, actual, same))
    assert verdict["verdict"] == "rejected_search"


def test_too_few_shared_videos_is_reported_not_judged():
    current = _evaluation(["a", "b", "c"], [1, 2, 3], [1, 2, 3])
    verdict = bench.compare(current, current)
    assert verdict["verdict"] == "insufficient_data"


def test_real_walk_forward_evaluation_runs_under_a_knob(dataset_rows, quick_training):
    plain = bench.evaluate({}, dataset_rows, warmup=40, stride=2)
    tweaked = bench.evaluate({"label_winsor": 0.2}, dataset_rows, warmup=40, stride=2)
    assert plain.scored == tweaked.scored > 0
    assert plain.video_ids == tweaked.video_ids
    assert plain.actual == tweaked.actual            # winsor never touches labels
    assert plain.predicted != tweaked.predicted      # but it does change the fit


# -- ledger ---------------------------------------------------------------------


def test_ledger_round_trip_history_and_era_counting(isolated_root):
    ledger.record_round({
        "round_id": "r1",
        "results": [
            {"name": "a", "changes": {"peer_window": 8},
             "comparison": {"verdict": "rejected_search",
                            "search": {"delta_pp": -1.0},
                            "confirm": {"era": "E1", "delta_pp": None}}},
            {"name": "b", "changes": {"label_winsor": 0.1},
             "comparison": {"verdict": "recommended",
                            "search": {"delta_pp": 3.0},
                            "confirm": {"era": "E1", "delta_pp": 2.0,
                                        "probability_better": 0.9}}},
        ],
    })
    assert ledger.experiments_on_era("E1") == 2
    assert ledger.experiments_on_era("E2") == 0
    assert [row["name"] for row in ledger.history()] == ["a", "b"]
    assert ledger.canonical({"peer_window": 8}) in ledger.tried_change_sets()
    assert ledger.find("r1", "b")["round_id"] == "r1"
    assert [r["name"] for r in ledger.latest_recommended()] == ["b"]


def test_canonical_ignores_key_and_list_order():
    a = {"exclude_feature_groups": ["text", "plan"], "peer_window": 8}
    b = {"peer_window": 8, "exclude_feature_groups": ["plan", "text"]}
    assert ledger.canonical(a) == ledger.canonical(b)


# -- the loop -------------------------------------------------------------------


@pytest.fixture
def scripted_round(monkeypatch, dataset_rows):
    """A round whose walk-forwards are instant and deterministic."""
    ids = [r["video_id"] for r in dataset_rows[:30]]
    actual = [(i % 5) / 4 for i in range(30)]
    current = _evaluation(ids, actual, [a * 0.7 + 0.15 for a in actual])

    monkeypatch.setattr(loop, "prepare", lambda *a, **k: (dataset_rows, current))

    def fake_evaluate(changes, base_rows=None, **kwargs):
        if changes.get("label_winsor") == 0.1:
            return _evaluation(ids, actual, [a * 0.9 + 0.05 for a in actual])
        return _evaluation(ids, actual, [0.5] * 30)

    monkeypatch.setattr(bench, "evaluate", fake_evaluate)
    return current


def test_a_round_asks_tests_records_and_changes_nothing(
    isolated_root, scripted_round
):
    write_config(isolated_root, {"brain": {"mode": "shadow"}})
    before = (isolated_root / "config.json").read_text(encoding="utf-8")
    asked = []

    def fake_claude(prompt):
        asked.append(prompt)
        return json.dumps({
            "diagnosis": "La senal dentro de shorts es debil.",
            "experiments": [
                {"name": "winsor_mas_fuerte", "hypothesis": "colas",
                 "changes": {"label_winsor": 0.1}},
                {"name": "ventana_ancha", "hypothesis": "estabilidad",
                 "changes": {"peer_window": 40}},
                {"name": "malicioso", "changes": {"os.system": "rm -rf /"}},
            ],
            "feature_ideas": [{"name": "retencion", "rationale": "menos ruido"}],
            "data_priorities": ["sincronizar retencion"],
        })

    result = loop.run_round(ask=fake_claude, max_experiments=4, progress=lambda m: None)

    assert len(asked) == 1
    assert result["ran"] is True
    verdicts = {r["name"]: r["comparison"]["verdict"] for r in result["results"]}
    assert verdicts["winsor_mas_fuerte"] == "recommended"
    assert verdicts["ventana_ancha"] != "recommended"
    assert "malicioso" not in verdicts
    assert any("unknown knob" in r["reason"] for r in result["rejected"])

    assert (isolated_root / "config.json").read_text(encoding="utf-8") == before
    assert ledger.load_rounds()[-1]["round_id"] == result["round_id"]


def test_dry_run_never_calls_claude(isolated_root, scripted_round):
    def must_not_be_called(prompt):
        raise AssertionError("dry run asked Claude")

    result = loop.run_round(ask=must_not_be_called, dry_run=True, progress=lambda m: None)
    assert result["dry_run"] is True
    assert "DOSSIER" in result["prompt"]
    assert ledger.load_rounds() == []


def test_an_exhausted_confirmation_era_runs_nothing(
    isolated_root, scripted_round, monkeypatch
):
    monkeypatch.setattr(ledger, "experiments_on_era", lambda era: ledger.ERA_BUDGET)

    def fake_claude(prompt):
        assert "at most 0 experiments" in prompt
        return json.dumps({"experiments": [
            {"name": "x", "changes": {"label_winsor": 0.1}}]})

    result = loop.run_round(ask=fake_claude, progress=lambda m: None)
    assert result["results"] == []
    assert "over the limit" in result["rejected"][0]["reason"]


def test_apply_adopts_only_recommended_experiments(isolated_root, scripted_round):
    write_config(isolated_root, {"verbose": True, "brain": {"mode": "shadow"}})

    def fake_claude(prompt):
        return json.dumps({"experiments": [
            {"name": "bueno", "changes": {"label_winsor": 0.1}},
            {"name": "malo", "changes": {"alpha_blend": 5}},
        ]})

    result = loop.run_round(ask=fake_claude, progress=lambda m: None)
    round_id = result["round_id"]

    with pytest.raises(PermissionError):
        loop.apply(f"{round_id}:malo")

    applied = loop.apply(f"{round_id}:bueno")
    assert applied["now"] == {"label_winsor": 0.1}
    config = json.loads((isolated_root / "config.json").read_text(encoding="utf-8"))
    assert config["verbose"] is True                        # nothing else touched
    assert config["brain"]["mode"] == "shadow"
    assert config["brain"]["training"] == {"label_winsor": 0.1}
    assert "python scripts/brain.py train" in applied["next_steps"]
    assert knobs.approved_overrides()[0] == {"label_winsor": 0.1}

    forced = loop.apply(f"{round_id}:malo", force=True)
    assert forced["forced"] is True


def test_revert_returns_to_code_defaults(isolated_root):
    write_config(isolated_root, {"brain": {"training": {"peer_window": 9}}})
    result = loop.revert()
    assert result["removed"] == {"peer_window": 9}
    assert knobs.approved_overrides()[0] == {}
    assert loop.revert()["removed"] == {}


def test_apply_without_config_json_explains_itself(isolated_root, scripted_round):
    ledger.record_round({"round_id": "r9", "results": [
        {"name": "x", "changes": {"label_winsor": 0.1},
         "comparison": {"verdict": "recommended"}}]})
    with pytest.raises(FileNotFoundError, match="config.json"):
        loop.apply("r9:x")


def test_adopted_knobs_reach_real_training(isolated_root, dataset_rows, quick_training):
    write_config(isolated_root, {"brain": {"training": {"label_winsor": 0.2}}})
    _, report = trainer.train(dataset_rows, targets=("normalized_views",))
    assert report["heads"]["normalized_views"]["label_winsor"] == 0.2
