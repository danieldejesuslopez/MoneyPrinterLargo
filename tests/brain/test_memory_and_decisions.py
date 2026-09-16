"""Channel memory, the decision ledger, and the public facade."""

from __future__ import annotations

import datetime as dt

import brain
from brain.experiments import decisions
from brain.memory import channel_memory

from conftest import NOW, make_account, write_cache


# -- channel memory -----------------------------------------------------


def test_clusters_group_the_channel_s_recurring_subjects(channel):
    clusters = channel_memory.cluster_topics(channel.published())
    assert clusters
    assert all(cluster["size"] >= 2 for cluster in clusters)
    assert any(cluster["label"] for cluster in clusters)


def test_cluster_performance_is_relative_to_the_channel(channel):
    clusters = channel_memory.cluster_topics(channel.published())
    ratios = [
        cluster["performance_vs_channel"] for cluster in clusters
        if cluster["performance_vs_channel"] is not None
    ]
    assert ratios
    assert min(ratios) > 0


def test_profile_captures_shape_patterns_and_hours(channel):
    profile = channel_memory.build_profile(channel)
    assert profile["videos"] == 60
    assert profile["baseline"]["median_views"]
    assert profile["title_patterns"]["winners"]["count"] > 0
    assert profile["retention_coverage"] == 1.0


def test_winning_titles_carry_the_curiosity_gap(channel):
    """The fixture's winners are the curiosity-gap titles, and only those."""
    patterns = channel_memory.build_profile(channel)["title_patterns"]
    assert patterns["winners"]["curiosity_share"] == 1.0
    assert patterns["losers"]["curiosity_share"] == 0.0


def test_profile_round_trips_to_disk(channel):
    profile = channel_memory.build_profile(channel)
    channel_memory.save_profile(profile)
    assert channel_memory.load_profile("acc-1")["videos"] == 60
    assert channel_memory.load_profile("nobody") == {}


def test_briefing_is_prompt_ready_text(channel):
    channel_memory.save_profile(channel_memory.build_profile(channel))
    text = channel_memory.briefing("acc-1")
    assert text
    assert "over-perform" in text or "Winning titles" in text


def test_briefing_is_empty_without_a_profile():
    assert channel_memory.briefing("unknown") == ""


def test_refresh_writes_one_profile_per_channel(isolated_root):
    write_cache(
        isolated_root,
        [make_account("acc-1", count=12),
         make_account("acc-2", count=12, account_suffix="b")],
    )
    assert len(brain.refresh_memory()) == 2


# -- decision ledger ----------------------------------------------------


def test_ranking_logs_a_decision_with_the_whole_candidate_set(channel):
    brain.rank_topics(
        "acc-1",
        ["La musica barroca europea", "Los volcanes submarinos", "Historia del ajedrez"],
    )
    logged = decisions.load_decisions("acc-1")
    assert len(logged) == 1
    assert logged[0]["stage"] == "topic"
    assert len(logged[0]["candidates"]) == 3
    assert logged[0]["brain_choice_label"]
    assert logged[0]["applied"] is False


def test_logging_can_be_switched_off(channel):
    brain.rank_topics("acc-1", ["Un tema"], log=False)
    assert decisions.load_decisions() == []


def test_unapplied_decisions_are_never_given_an_outcome(channel):
    brain.rank_topics("acc-1", ["El misterio del cometa Halley"])
    result = decisions.reconcile("acc-1")
    assert result["reconciled"] == 0
    assert decisions.load_decisions()[0]["actual_score"] is None


def test_an_applied_decision_is_scored_against_the_published_video(isolated_root):
    """A pick that shipped and has numbers gets a real prediction error."""
    account = make_account(count=30)
    write_cache(isolated_root, [account])

    from brain.data import collector
    from brain.schema import DecisionRecord

    channel = collector.find_channel("acc-1", now=NOW)
    published = channel.published()[-1]

    decisions.log_decision(
        DecisionRecord(
            account_id="acc-1",
            stage="topic",
            created_at=(published.published_at - dt.timedelta(days=1)).isoformat(),
            brain_choice_id="c1",
            brain_choice_label=published.title,
            predicted_score=7.0,
            applied=True,
        )
    )
    result = decisions.reconcile("acc-1")
    assert result["reconciled"] == 1

    row = decisions.load_decisions()[0]
    assert row["video_id"] == published.video_id
    assert row["actual_score"] is not None
    assert row["prediction_error"] == round(row["actual_score"] - 7.0, 3)
    assert row["reconciled_at"]


def test_reconcile_is_idempotent(isolated_root):
    account = make_account(count=30)
    write_cache(isolated_root, [account])

    from brain.data import collector
    from brain.schema import DecisionRecord

    channel = collector.find_channel("acc-1", now=NOW)
    published = channel.published()[-1]
    decisions.log_decision(
        DecisionRecord(
            account_id="acc-1",
            created_at=(published.published_at - dt.timedelta(days=1)).isoformat(),
            brain_choice_label=published.title,
            predicted_score=7.0,
            applied=True,
        )
    )
    assert decisions.reconcile()["reconciled"] == 1
    assert decisions.reconcile()["reconciled"] == 0


def test_shadow_report_refuses_to_conclude_from_too_little(channel):
    for topic in ("uno", "dos", "tres"):
        brain.rank_topics("acc-1", [topic])
    report = decisions.shadow_report("acc-1")
    assert report["decisions"] == 3
    assert report["reconciled"] == 0
    assert "none with a measured outcome" in report["message"]


def test_shadow_report_on_an_empty_ledger():
    assert decisions.shadow_report()["decisions"] == 0


# -- facade -------------------------------------------------------------


def test_suggest_topic_stays_silent_in_shadow_mode(channel):
    assert brain.mode("acc-1") == "shadow"
    assert brain.suggest_topic("acc-1", ["Un tema", "Otro"]) == ""


def test_suggest_topic_stays_silent_when_disabled(channel, monkeypatch):
    monkeypatch.setenv("MP_BRAIN_ENABLED", "false")
    monkeypatch.setenv("MP_BRAIN_MODE", "auto")
    assert brain.suggest_topic("acc-1", ["Un tema"]) == ""


def test_suggest_topic_abstains_when_unsure(channel, monkeypatch):
    """Deciding mode is not enough — confidence has to clear the floor."""
    monkeypatch.setenv("MP_BRAIN_MODE", "auto")
    assert brain.suggest_topic("acc-1", ["Un tema", "Otro tema"]) == ""


def test_suggest_topic_picks_once_it_is_confident(
    channel, monkeypatch, dataset_rows, isolated_root
):
    import json

    from brain.training import train as trainer

    trainer.train_and_register(dataset_rows)
    (isolated_root / "config.json").write_text(
        json.dumps({"brain": {"mode": "auto", "abstain_confidence": 0.1}}),
        encoding="utf-8",
    )
    assert brain.mode("acc-1") == "auto"
    chosen = brain.suggest_topic(
        "acc-1",
        ["Cosas del espacio", "9 datos que nadie te conto sobre el cometa Halley"],
    )
    assert chosen == "9 datos que nadie te conto sobre el cometa Halley"


def test_suggest_topic_never_raises(monkeypatch):
    monkeypatch.setenv("MP_BRAIN_MODE", "auto")
    monkeypatch.setattr(
        "brain.rank_topics",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert brain.suggest_topic("acc-1", ["algo"]) == ""


def test_status_reports_everything_a_dashboard_needs(channel):
    report = brain.status("acc-1")
    assert report["brain_version"]
    assert report["feature_count"] > 0
    assert report["tier"] == "heuristic"
    assert report["channels"][0]["published"] == 60
    assert "gaps" in report and "shadow" in report


def test_observe_records_a_snapshot(channel):
    result = brain.observe(now=NOW)
    assert result["written"] == 60
