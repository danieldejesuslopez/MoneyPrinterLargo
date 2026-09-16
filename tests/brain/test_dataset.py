"""Collector, normalizer and dataset builder."""

from __future__ import annotations

import datetime as dt

from brain.data import collector, dataset as ds, validators
from brain.data import normalizer as norm
from brain.schema import VideoRecord

from conftest import NOW, make_account, make_video, write_cache


# -- collector ----------------------------------------------------------


def test_placeholder_entries_are_not_treated_as_published(isolated_root):
    account = make_account(count=4)
    account["videos"].append(
        make_video(99, strong=True, published=NOW, views=0, url="uploading...")
    )
    write_cache(isolated_root, [account])
    channel = collector.find_channel("acc-1", now=NOW)
    assert len(channel.videos) == 5
    assert len(channel.published()) == 4


def test_missing_cache_yields_no_channels(isolated_root):
    assert collector.load_channels() == []


def test_snapshots_skip_unchanged_counters(channel):
    first = collector.record_snapshot(now=NOW)
    second = collector.record_snapshot(now=NOW + dt.timedelta(hours=1))
    assert first["written"] == 60
    assert second["written"] == 0
    assert second["skipped_unchanged"] == 60


def test_windowed_views_never_interpolates():
    snapshots = [
        {"age_hours": 30.0, "views": 500.0},
        {"age_hours": 200.0, "views": 900.0},
    ]
    windowed = collector.windowed_views(snapshots)
    # No reading inside the first 24h, so there is no views_24h at all.
    assert "views_24h" not in windowed
    assert windowed["views_48h"] == 500.0
    assert windowed["views_168h"] == 500.0
    assert windowed["views_720h"] == 900.0


def test_view_velocity_is_per_hour():
    velocity = collector.view_velocity({"views_24h": 480.0})
    assert velocity["velocity_24h"] == 20.0


# -- normalizer ---------------------------------------------------------


def test_peer_window_only_looks_backwards(channel):
    videos = channel.published()
    target = videos[10]
    peers = norm.peer_window(target, videos)
    assert peers
    assert all(peer.published_at < target.published_at for peer in peers)


def test_normalized_views_is_relative_to_recent_peers(channel):
    videos = channel.published()
    target = videos[-1]
    peers = norm.peer_window(target, videos)
    value, basis = norm.normalized_views(target, peers)
    assert basis == "channel"
    assert 0.5 < value < 3.0


def test_normalization_falls_back_to_global_then_none():
    record = VideoRecord(video_id="x", views=200.0, published_at=NOW)
    value, basis = norm.normalized_views(record, [], global_median=100.0)
    assert (value, basis) == (2.0, "global")
    assert norm.normalized_views(record, [])[1] == "none"


def test_missing_metrics_produce_none_labels_not_zeros():
    record = VideoRecord(video_id="x", views=100.0, published_at=NOW)
    labels = norm.build_labels(record, [], global_median=100.0)
    assert labels["targets"]["ctr"] is None
    assert labels["targets"]["retention"] is None
    assert labels["targets"]["like_rate"] is None


def test_maturity_is_measured_at_the_sync_not_now():
    published = dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc)
    fresh_sync = VideoRecord(
        video_id="x", published_at=published,
        stats_synced_at=published + dt.timedelta(hours=2),
    )
    late_sync = VideoRecord(
        video_id="y", published_at=published,
        stats_synced_at=published + dt.timedelta(days=30),
    )
    # Both are a year old, but only one was actually *measured* late.
    assert norm.is_mature(fresh_sync, NOW) is False
    assert norm.is_mature(late_sync, NOW) is True


def test_reward_renormalizes_over_available_targets():
    weights = {"normalized_views": 0.5, "retention": 0.5}
    both = norm.reward({"normalized_views": 3.0, "retention": 1.0}, weights)
    one = norm.reward({"normalized_views": 3.0}, weights)
    assert both == 1.0
    assert one == 1.0  # not halved just because retention is missing
    assert norm.reward({}, weights) is None


def test_momentum_needs_enough_history():
    assert norm.momentum([], NOW) is None


# -- dataset ------------------------------------------------------------


def test_rows_carry_features_labels_and_provenance(dataset_rows):
    row = dataset_rows[-1]
    assert row["feature_schema"]
    assert row["coverage"] > 0
    assert row["targets"]["retention"] == 0.55 or row["targets"]["retention"] == 0.35
    assert row["success_class"] in {
        "failed", "weak", "normal", "good", "breakout", "viral",
    }


def test_candidate_from_record_drops_results():
    record = VideoRecord(
        video_id="x", title="t", topic="s", views=9999.0,
        avg_percentage_viewed=88.0, published_at=NOW,
        raw={"view_count": 9999, "hook_variants": [{"hook": "h", "score": 8}]},
    )
    candidate = ds.candidate_from_record(record)
    assert candidate.hook == "h"
    assert not hasattr(candidate, "views")
    assert candidate.planned_publish_at == NOW


def test_time_split_is_chronological(dataset_rows):
    split = ds.time_split(dataset_rows, validation_fraction=0.2)
    assert len(split["train"]) == 48
    assert len(split["validation"]) == 12
    last_train = split["train"][-1]["published_at"]
    first_validation = split["validation"][0]["published_at"]
    assert last_train <= first_validation


def test_to_matrix_drops_unlabeled_rows_instead_of_zero_filling(dataset_rows):
    rows = [dict(row) for row in dataset_rows]
    rows[0]["targets"] = dict(rows[0]["targets"], normalized_views=None)
    features, labels, kept = ds.to_matrix(rows, "normalized_views")
    assert len(features) == len(labels) == len(kept) == len(rows) - 1


def test_log_targets_round_trip():
    assert abs(ds.inverse_target("normalized_views",
                                 ds.transform_target("normalized_views", 2.5)) - 2.5) < 1e-9
    assert ds.transform_target("retention", 0.4) == 0.4


def test_build_dataset_writes_and_reloads(channel):
    summary = ds.build_dataset([channel], now=NOW)
    assert summary["rows"] == 60
    reloaded = ds.load_dataset()
    assert len(reloaded) == 60


# -- validators ---------------------------------------------------------


def test_validation_flags_unknown_targets_and_duplicates(dataset_rows):
    rows = [dict(row) for row in dataset_rows]
    rows[0] = dict(rows[0], targets=dict(rows[0]["targets"], made_up=1.0))
    rows.append(dict(rows[1]))
    report = validators.validate_dataset(rows)
    assert report["invalid_rows"] == 1
    assert report["duplicate_video_ids"]


def test_data_gaps_report_names_what_is_missing(channel):
    report = validators.data_gaps([channel])
    fields = {gap["field"]: gap for gap in report["gaps"]}
    assert fields["ctr"]["coverage"] == 0.0
    assert fields["duration_seconds"]["coverage"] == 1.0
    assert any("CTR" in note for note in report["notes"])
