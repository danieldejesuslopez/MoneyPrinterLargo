"""The leakage guarantee (spec §114, §148).

The single most important property in this package: a prediction made at
time T must never depend on anything that only became knowable after T. If
this file passes, backtests and validation scores mean something. If it
fails, every other number Brain reports is inflated and worthless.

Three independent angles, because one of them alone could be satisfied by a
mistake:

  - structural: no post-publish feature is in the model input;
  - functional: rewriting a video's results does not move its features;
  - temporal: a video's features do not depend on videos published later.
"""

from __future__ import annotations

import copy
import datetime as dt

from brain.data import dataset as ds, validators
from brain.features import registry as fr
from brain.features.base import POST_PUBLISH
from brain.schema import ChannelContext

from conftest import NOW, make_account, write_cache


def test_no_post_publish_feature_reaches_the_model_input():
    assert [] == [
        spec.name for spec in fr.INPUT_SPECS if spec.availability == POST_PUBLISH
    ]


def test_inflating_results_does_not_change_any_feature(channel):
    """Multiply every result metric by 37 — the vector must be identical."""
    report = validators.leakage_report(sample_rows=25)
    assert report["clean"], report
    assert report["records_checked"] == 25


def test_features_ignore_views_likes_comments_and_retention(channel):
    record = channel.published()[30]
    baseline = fr.vectorize(ds.candidate_from_record(record), channel).values

    tampered = copy.copy(record)
    tampered.views = 10_000_000.0
    tampered.likes = 500_000.0
    tampered.comments = 90_000.0
    tampered.avg_percentage_viewed = 99.9
    tampered.subscribers_gained = 4242.0
    tampered.ctr = 51.0
    tampered.raw = dict(record.raw)
    tampered.raw["view_count"] = 10_000_000

    assert fr.vectorize(ds.candidate_from_record(tampered), channel).values == baseline


def test_a_future_hit_cannot_change_an_earlier_row(isolated_root):
    """Channel features are computed from history *before* the candidate."""
    account = make_account(count=30)
    write_cache(isolated_root, [account])

    from brain.data import collector

    channel = collector.find_channel("acc-1", now=NOW)
    early = channel.published()[5]
    before = fr.vectorize(ds.candidate_from_record(early), channel).values

    # A monster video published after it, with 100x the views.
    account["videos"].append(
        {
            "title": "Un exito posterior gigantesco",
            "subject": "exito posterior",
            "url": "https://youtu.be/zzzzzzzzzzz",
            "date": (NOW - dt.timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
            "is_short": True,
            "view_count": 5_000_000,
            "stats_synced_at": NOW.strftime("%Y-%m-%d %H:%M:%S"),
        }
    )
    write_cache(isolated_root, [account])
    channel_after = collector.find_channel("acc-1", now=NOW)
    after = fr.vectorize(ds.candidate_from_record(early), channel_after).values

    assert after == before


def test_labels_are_normalized_against_prior_videos_only(dataset_rows):
    """A row's peer count can never exceed the videos published before it."""
    for index, row in enumerate(dataset_rows):
        assert row["peer_count"] <= index


def test_empty_context_yields_defaults_not_crashes():
    """A channel with no history still produces a full, finite vector."""
    from brain.schema import Candidate

    vector = fr.vectorize(Candidate(topic="tema"), ChannelContext())
    assert len(vector.values) == fr.FEATURE_COUNT
    assert all(isinstance(value, float) for value in vector.values)
