"""The research conversation: menu of analyses, clock, memory, and honesty.

A scripted Claude plays every role the real one might: asks for sensible
analyses, asks for ones that do not exist, repeats itself, returns garbage,
and in the closing report recommends a change it never tested. Each test
checks the session keeps its promises regardless.
"""

from __future__ import annotations

import json

import pytest

from brain.data import dataset as ds
from brain.research import analyses, bench, ledger, loop, session

TARGET = "normalized_views"


def _context_from(rows):
    """An evaluation built from real rows: predictions near the truth."""
    rows = sorted(rows, key=lambda r: str(r.get("published_at") or ""))[20:]
    actual = [ds.transform_target(TARGET, r["targets"][TARGET]) for r in rows]
    current = bench.Evaluation(
        changes={},
        video_ids=[str(r["video_id"]) for r in rows],
        actual=actual,
        predicted=[a * 0.8 + 0.1 for a in actual],
        null=[sum(actual) / len(actual)] * len(actual),
        format_null=[sum(actual) / len(actual)] * len(actual),
        kinds=[r["kind"] for r in rows],
    )
    return current


@pytest.fixture
def ctx(dataset_rows):
    return analyses.Context(list(dataset_rows), _context_from(dataset_rows))


@pytest.fixture
def instant_session(monkeypatch, dataset_rows):
    current = _context_from(dataset_rows)
    monkeypatch.setattr(loop, "prepare", lambda *a, **k: (list(dataset_rows), current))
    return current


class FakeClock:
    def __init__(self, step=10.0):
        self.now = 0.0
        self.step = step

    def __call__(self):
        self.now += self.step
        return self.now


class ScriptedClaude:
    """Returns a queued reply per call and remembers every prompt it saw."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, prompt, first):
        self.calls.append({"prompt": prompt, "first": first})
        if not self.replies:
            return json.dumps({"reading": "fin", "findings": [], "done": True})
        reply = self.replies.pop(0)
        return reply if isinstance(reply, str) else json.dumps(reply)


def turn(analysis=None, params=None, done=False, findings=("x",)):
    reply = {"reading": "leo", "findings": list(findings), "done": done}
    if analysis:
        reply["next"] = {"analysis": analysis, "params": params or {}, "why": "porque"}
    return reply


# -- analyses -------------------------------------------------------------------


@pytest.mark.parametrize("name,params", [
    ("worst_predictions", {"count": 5, "direction": "over"}),
    ("drift", {"periods": 3}),
    ("residual_correlations", {"top": 8, "format": "short"}),
    # the synthetic channel has no thumbnails, so profile a title feature
    ("feature_profile", {"feature": "title_chars", "bins": 3}),
    ("feature_list", {"group": "thumbnail"}),
    ("topic_clusters", {"top": 5}),
    ("calendar", {}),
    ("title_examples", {"group": "worst", "count": 4}),
])
def test_every_cheap_analysis_runs_on_real_rows(ctx, name, params):
    result = analyses.run(ctx, name, params)
    assert isinstance(result, dict) and result
    json.dumps(result, default=str)  # must be sendable to Claude


def test_worst_predictions_reports_real_multiples_and_direction(ctx):
    result = analyses.run(ctx, "worst_predictions", {"count": 3, "direction": "under"})
    assert len(result["videos"]) <= 3
    assert all(v["residual_log"] < 0 for v in result["videos"])
    assert all("thumb_edge_density" in v["features"] for v in result["videos"])


@pytest.mark.parametrize("name,params,fragment", [
    ("rm_rf", {}, "unknown analysis"),
    ("worst_predictions", {"count": 99}, "between 1 and 15"),
    ("worst_predictions", {"direction": "sideways"}, "must be one of"),
    ("feature_profile", {"feature": "__class__"}, "unknown feature"),
    ("drift", {"periods": "tres"}, "integer"),
    ("worst_predictions", ["not", "an", "object"], "JSON object"),
])
def test_analyses_reject_bad_requests_with_a_readable_reason(ctx, name, params, fragment):
    with pytest.raises(analyses.AnalysisError, match=fragment):
        analyses.run(ctx, name, params)


def test_model_weights_explains_when_there_is_no_model(ctx):
    with pytest.raises(analyses.AnalysisError, match="no trained champion"):
        analyses.run(ctx, "model_weights", {})


def test_experiment_validates_before_spending_anything(ctx, monkeypatch):
    def must_not_run(*a, **k):
        raise AssertionError("evaluated an invalid experiment")

    monkeypatch.setattr(bench, "evaluate", must_not_run)
    with pytest.raises(analyses.AnalysisError, match="unknown knob"):
        analyses.run(ctx, "experiment", {"changes": {"shell": "rm -rf"}})
    with pytest.raises(analyses.AnalysisError, match="at least one knob"):
        analyses.run(ctx, "experiment", {"changes": {}})


def test_experiment_is_written_to_the_ledger_immediately(ctx, monkeypatch):
    monkeypatch.setattr(bench, "evaluate", lambda changes, rows=None, **k: ctx.current)
    result = analyses.run(ctx, "experiment",
                          {"name": "prueba", "changes": {"label_winsor": 0.1}})
    assert result["changes"] == {"label_winsor": 0.1}
    assert ledger.canonical({"label_winsor": 0.1}) in ledger.tried_change_sets()
    with pytest.raises(analyses.AnalysisError, match="already tried"):
        analyses.run(ctx, "experiment", {"changes": {"label_winsor": 0.1}})


def test_experiment_refuses_once_the_budget_is_spent(ctx, monkeypatch):
    monkeypatch.setattr(ledger, "experiments_on_era", lambda era: ledger.ERA_BUDGET)
    with pytest.raises(analyses.AnalysisError, match="budget is exhausted"):
        analyses.run(ctx, "experiment", {"changes": {"alpha_blend": 2}})


# -- the conversation -------------------------------------------------------------


def test_a_session_converses_filters_and_reports(isolated_root, instant_session):
    (isolated_root / "config.json").write_text('{"brain": {"mode": "shadow"}}',
                                               encoding="utf-8")
    before = (isolated_root / "config.json").read_text(encoding="utf-8")

    claude = ScriptedClaude([
        turn("worst_predictions", {"count": 4}),
        turn("hack_the_planet", {}),
        turn("worst_predictions", {"count": 4}),          # exact repeat
        turn("drift", {"periods": 3}),
        turn(done=True, findings=["la miniatura importa"]),   # challenged once
        turn(done=True, findings=["la miniatura importa"]),   # accepted
        {   # closing report tries to recommend something never tested
            "summary": "Poca senal dentro de shorts.",
            "key_findings": [{"finding": "deriva reciente", "evidence": "drift",
                              "confidence": "media"}],
            "recommended_changes": [{"changes": {"peer_window": 6}, "evidence": "intuicion"}],
            "feature_ideas": [{"name": "retencion", "rationale": "r", "data_needed": "d"}],
            "data_priorities": ["retencion"],
        },
    ])

    result = session.run_session(minutes=60, max_turns=20, ask_turn=claude,
                                 clock=FakeClock(), progress=lambda m: None)

    assert claude.calls[0]["first"] is True
    assert all(call["first"] is False for call in claude.calls[1:])
    assert "ANALYSIS MENU" in claude.calls[0]["prompt"]
    assert "RESULT of worst_predictions" in claude.calls[1]["prompt"]
    assert "unknown analysis" in claude.calls[2]["prompt"]
    assert "already requested exactly this" in claude.calls[3]["prompt"]
    assert "RESULT of drift" in claude.calls[4]["prompt"]
    assert "try to break your own strongest finding" in claude.calls[5]["prompt"]

    assert result["ended_by"] == "claude"
    report = result["report"]
    assert report["recommended_changes"] == []
    assert report["unsupported_recommendations"][0]["changes"] == {"peer_window": 6}

    for name in ("transcript.md", "turns.jsonl", "report.md", "report.json"):
        assert (isolated_root / ".mp" / "brain" / "sessions" / result["session"] / name).exists()
    report_md = (isolated_root / ".mp" / "brain" / "sessions" / result["session"]
                 / "report.md").read_text(encoding="utf-8")
    assert "SIN respaldo experimental" in report_md
    assert (isolated_root / "config.json").read_text(encoding="utf-8") == before


def test_the_clock_ends_the_session_and_still_gets_a_report(isolated_root, instant_session):
    clock = FakeClock(step=600.0)     # ten minutes pass on every clock read
    claude = ScriptedClaude([turn("calendar", {})] * 50 + [{"summary": "cierre"}])
    result = session.run_session(minutes=120, max_turns=50, ask_turn=claude,
                                 clock=clock, progress=lambda m: None)
    assert result["ended_by"] == "time"
    assert result["turns"] < 50
    assert "session is over" in claude.calls[-1]["prompt"]


def test_three_unreadable_replies_in_a_row_end_the_session(isolated_root, instant_session):
    claude = ScriptedClaude(["no json", "tampoco", "nada", {"summary": "s"}])
    result = session.run_session(minutes=60, max_turns=20, ask_turn=claude,
                                 clock=FakeClock(), progress=lambda m: None)
    assert result["ended_by"] == "failures"


def test_cli_errors_are_retried_then_give_up(isolated_root, instant_session, monkeypatch):
    monkeypatch.setattr(session.time, "sleep", lambda s: None)
    attempts = []

    def broken(prompt, first):
        attempts.append(first)
        raise RuntimeError("red caida")

    result = session.run_session(minutes=60, max_turns=20, ask_turn=broken,
                                 clock=FakeClock(), progress=lambda m: None)
    assert result["ended_by"] == "failures"
    assert attempts == [True, True, True]          # never pretends the session exists
    assert "no llego a hablar" in result["report"]["summary"]


def test_an_experiment_needs_time_left_to_start(isolated_root, instant_session, monkeypatch):
    def must_not_run(*a, **k):
        raise AssertionError("experiment started with no time left")

    monkeypatch.setattr(bench, "evaluate", must_not_run)
    clock = FakeClock(step=1.0)
    # 4 minutes total: under the experiment floor plus the synthesis reserve
    claude = ScriptedClaude([
        turn("experiment", {"changes": {"label_winsor": 0.1}}),
        turn(done=True),
        {"summary": "s"},
    ])
    result = session.run_session(minutes=4, max_turns=10, ask_turn=claude,
                                 clock=clock, progress=lambda m: None)
    assert "not enough time left" in claude.calls[1]["prompt"]
    assert result["experiments"] == []


def test_a_passed_experiment_survives_into_the_report(isolated_root, instant_session, monkeypatch):
    current = instant_session
    better = bench.Evaluation(
        changes={}, video_ids=current.video_ids, actual=current.actual,
        predicted=current.actual, null=current.null, format_null=current.format_null,
        kinds=current.kinds,
    )
    monkeypatch.setattr(bench, "evaluate", lambda changes, rows=None, **k: better)

    claude = ScriptedClaude([
        turn("experiment", {"name": "perfecto", "changes": {"label_winsor": 0.15}}),
        turn(done=True),
        turn(done=True),
        {"summary": "s",
         "recommended_changes": [
             {"changes": {"label_winsor": 0.15}, "evidence": "paso ambas eras"},
             {"changes": {"alpha_blend": 2}, "evidence": "sin probar"},
         ]},
    ])
    result = session.run_session(minutes=60, max_turns=10, ask_turn=claude,
                                 clock=FakeClock(), progress=lambda m: None)
    assert result["experiments"][0]["verdict"] == "recommended"
    assert result["report"]["recommended_changes"] == [
        {"changes": {"label_winsor": 0.15}, "evidence": "paso ambas eras"}]
    assert result["report"]["unsupported_recommendations"] == [
        {"changes": {"alpha_blend": 2}, "evidence": "sin probar"}]


def test_session_cli_arguments_disable_tools_and_resume(monkeypatch):
    seen = []

    class Done:
        returncode = 0
        stdout = "{}"
        stderr = ""

    monkeypatch.setattr(session.subprocess, "run",
                        lambda args, **k: seen.append(args) or Done())
    ask = session.cli_session("11111111-1111-1111-1111-111111111111")
    ask("hola", True)
    ask("sigue", False)
    first, second = seen
    assert first[first.index("--session-id") + 1].startswith("1111")
    assert second[second.index("--resume") + 1].startswith("1111")
    for args in seen:
        assert args[args.index("--tools") + 1] == ""
        assert "--no-session-persistence" not in args   # memory is the point


def test_an_early_done_is_challenged_once_then_accepted(isolated_root, instant_session):
    claude = ScriptedClaude([
        turn("calendar", {}),
        turn(done=True),            # far too early: challenged
        turn("drift", {"periods": 2}),
        turn(done=True),            # second time: accepted
        {"summary": "s"},
    ])
    result = session.run_session(minutes=60, max_turns=20, ask_turn=claude,
                                 clock=FakeClock(), progress=lambda m: None)
    assert "try to break your own strongest finding" in claude.calls[2]["prompt"]
    assert "RESULT of drift" in claude.calls[3]["prompt"]
    assert result["ended_by"] == "claude"


def test_experiment_time_budget_follows_the_measured_walk_forward(
    isolated_root, instant_session, monkeypatch
):
    """A fast walk-forward must not lock experiments out of the last minutes."""
    current = instant_session
    monkeypatch.setattr(bench, "evaluate", lambda changes, rows=None, **k: current)
    claude = ScriptedClaude([
        turn("experiment", {"name": "e", "changes": {"alpha_blend": 2}}),
        turn(done=True),
        {"summary": "s"},
    ])
    # 10 minutes, instant measurement: floor is 90s + 180s reserve, so allowed
    result = session.run_session(minutes=10, max_turns=5, ask_turn=claude,
                                 clock=FakeClock(step=1.0), progress=lambda m: None)
    assert result["experiments"] and result["experiments"][0]["changes"] == {"alpha_blend": 2}


# -- phase two: strategy, duplicates, continuing a conversation ---------------------


def test_duplicates_finds_repeated_topics_with_their_gaps(ctx):
    # the fixture's winners all share one near-identical title shape
    result = analyses.run(ctx, "duplicates", {"threshold": 0.5})
    assert result["repeated_topics"] >= 1
    first_group = result["groups"][0]["copies"]
    assert first_group[0]["copy"] == 1 and first_group[0]["days_after_first"] == 0.0
    assert all(c["days_after_first"] >= 0 for c in first_group)
    assert result["median_multiple_later_copies"] is not None


def test_duplicates_rejects_a_silly_threshold(ctx):
    with pytest.raises(analyses.AnalysisError, match="between 0.3 and 0.8"):
        analyses.run(ctx, "duplicates", {"threshold": 0.05})


def test_compare_groups_separates_a_real_difference(ctx):
    # fixture: long numbered titles are the winners, short flat ones the losers
    result = analyses.run(ctx, "compare_groups",
                          {"feature": "title_chars", "split": "median", "format": "all"})
    assert result["low_group"]["n"] + result["high_group"]["n"] >= 16
    low, high = result["high_minus_low_ci95"]
    assert low <= result["high_minus_low_mean_log1p"] <= high
    assert result["distinguishable_from_zero"] is True
    assert result["median_multiple_ratio_high_over_low"] > 1


def test_compare_groups_needs_a_known_feature(ctx):
    with pytest.raises(analyses.AnalysisError, match="unknown feature"):
        analyses.run(ctx, "compare_groups", {"feature": "import os"})


def test_strategy_goal_changes_the_question_and_the_report(isolated_root, instant_session):
    claude = ScriptedClaude([
        turn("duplicates", {}),
        turn(done=True), turn(done=True),
        {"summary": "s",
         "republishing_rule": {"rule": "no republicar antes de 30 dias",
                               "evidence": "duplicates", "confidence": "baja"},
         "content_guidelines": [{"guideline": "portadas limpias", "evidence": "compare_groups",
                                 "proven": False, "confidence": "baja"}],
         "learning_plan": [{"step": 1, "videos": 10, "what_to_vary": "portada",
                            "what_to_measure": "multiple", "decision_rule": "mantener si"}]},
    ])
    result = session.run_session(minutes=60, max_turns=10, ask_turn=claude, goal="strategy",
                                 clock=FakeClock(), progress=lambda m: None)
    assert "what the CREATOR" in claude.calls[0]["prompt"]
    assert "Republishing" in claude.calls[0]["prompt"]
    assert "republishing_rule" in claude.calls[-1]["prompt"]
    report_md = (isolated_root / ".mp" / "brain" / "sessions" / result["session"]
                 / "report.md").read_text(encoding="utf-8")
    assert "Regla de republicacion" in report_md
    assert "hipotesis a probar" in report_md          # not proven -> not a rule
    assert "Plan de aprendizaje" in report_md


def test_a_session_can_continue_an_earlier_conversation(isolated_root, instant_session):
    earlier = session.sessions_dir() + "/20260101-000000"
    import os
    os.makedirs(earlier, exist_ok=True)
    with open(earlier + "/report.json", "w", encoding="utf-8") as f:
        json.dump({"claude_session_id": "22222222-2222-2222-2222-222222222222"}, f)

    claude = ScriptedClaude([turn(done=True), turn(done=True), {"summary": "s"}])
    result = session.run_session(minutes=60, max_turns=5, ask_turn=claude,
                                 goal="strategy", continue_from="20260101-000000",
                                 clock=FakeClock(), progress=lambda m: None)

    assert claude.calls[0]["first"] is False            # resume, never re-create
    assert "NEW PHASE" in claude.calls[0]["prompt"]
    assert "DOSSIER" not in claude.calls[0]["prompt"]   # Claude already has it
    assert "duplicates" in claude.calls[0]["prompt"]     # but sees the new menu
    assert result["claude_session_id"] == "22222222-2222-2222-2222-222222222222"
    assert result["continued_from"] == "20260101-000000"


def test_continuing_a_missing_session_fails_clearly(isolated_root, instant_session):
    with pytest.raises(FileNotFoundError, match="no finished session"):
        session.run_session(minutes=5, continue_from="does-not-exist",
                            ask_turn=ScriptedClaude([]), clock=FakeClock(),
                            progress=lambda m: None)


def test_an_unknown_goal_is_refused():
    with pytest.raises(ValueError, match="goal must be one of"):
        session.run_session(goal="world_domination", ask_turn=ScriptedClaude([]))
