"""Reference channels: the store, and how they reach (and don't reach) Brain.

The invariant worth protecting: a reference channel is training material and
nothing else. It must never appear where the pipeline lists channels it can
publish to, must never be snapshotted, and must never distort the cold-start
baseline used for the user's own small channels.
"""

from __future__ import annotations

import datetime as dt

from brain.data import collector, dataset as ds
from brain.data import reference

from conftest import NOW, make_account, write_cache


def _ref_account(account_id: str = "ref:other", count: int = 30,
                 views_base: float = 500_000.0) -> dict:
    """A big reference channel, orders of magnitude above the user's."""
    account = make_account(account_id, count=count, account_suffix="r")
    for index, video in enumerate(account["videos"]):
        video["view_count"] = int(views_base + index * 1000)
    account["reference"] = True
    account["youtube_handle"] = "@other"
    account["subscriber_count"] = 3_000_000
    return account


def install_reference(accounts: list[dict]) -> None:
    reference.save_reference({"accounts": accounts})


# -- pure helpers -------------------------------------------------------


def test_normalize_handle_accepts_every_shape():
    assert reference.normalize_handle("@savirox") == "@savirox"
    assert reference.normalize_handle("https://www.youtube.com/@savirox") == "@savirox"
    assert reference.normalize_handle("youtube.com/@savirox/videos") == "@savirox"


def test_split_limit_keeps_the_channel_s_format_mix():
    # 913 longs + 361 shorts, budget 300 -> roughly the same 72/28 split.
    longs, shorts = reference._split_limit(913, 361, 300)
    assert longs + shorts == 300
    assert 210 <= longs <= 220
    assert 80 <= shorts <= 90


def test_split_limit_takes_everything_when_it_fits():
    assert reference._split_limit(60, 40, 300) == (60, 40)


def test_split_limit_never_asks_for_more_than_exists():
    longs, shorts = reference._split_limit(5, 400, 100)
    assert longs <= 5 and shorts <= 400 and longs + shorts <= 100


# -- store --------------------------------------------------------------


def test_store_round_trips_and_summarizes(isolated_root):
    install_reference([_ref_account()])
    rows = reference.summary()
    assert len(rows) == 1
    assert rows[0]["handle"] == "@other"
    assert rows[0]["videos"] == 30
    assert rows[0]["subscribers"] == 3_000_000
    assert rows[0]["median_views"] > 100_000


def test_missing_store_is_empty_not_an_error(isolated_root):
    assert reference.reference_accounts() == []
    assert reference.summary() == []


def test_remove_is_idempotent(isolated_root):
    install_reference([_ref_account()])
    assert reference.remove_channel("@other") is True
    assert reference.remove_channel("@other") is False
    assert reference.reference_accounts() == []


def test_reference_channels_are_flagged(isolated_root):
    install_reference([_ref_account()])
    channel = reference.reference_channels(NOW)[0]
    assert channel.is_reference is True
    assert len(channel.published()) == 30


# -- isolation from the user's own channels -----------------------------


def test_collector_hides_reference_channels_by_default(isolated_root):
    write_cache(isolated_root, [make_account("mine", count=10)])
    install_reference([_ref_account()])

    own = collector.load_channels(now=NOW)
    assert [c.account_id for c in own] == ["mine"]

    both = collector.load_channels(now=NOW, include_reference=True)
    assert {c.account_id for c in both} == {"mine", "ref:other"}
    assert [c.is_reference for c in both] == [False, True]


def test_snapshots_never_record_reference_videos(isolated_root):
    write_cache(isolated_root, [make_account("mine", count=10)])
    install_reference([_ref_account()])
    assert collector.record_snapshot(now=NOW)["videos"] == 10


def test_find_channel_does_not_resolve_a_reference_id(isolated_root):
    write_cache(isolated_root, [make_account("mine", count=5)])
    install_reference([_ref_account()])
    assert collector.find_channel("ref:other", now=NOW) is None


# -- effect on the dataset ----------------------------------------------


def test_global_median_ignores_reference_channels(isolated_root):
    """A 3M-sub channel must not define "normal" for a 258-sub one."""
    write_cache(isolated_root, [make_account("mine", count=10)])
    install_reference([_ref_account()])
    channels = collector.load_channels(now=NOW, include_reference=True)

    median = ds.global_median_views(channels)
    assert median is not None
    assert median < 10_000  # the user's own scale, not the reference channel's


def test_dataset_includes_and_tags_reference_rows(isolated_root):
    write_cache(isolated_root, [make_account("mine", count=10)])
    install_reference([_ref_account()])

    rows = ds.build_rows(now=NOW)
    own = [r for r in rows if not r["is_reference"]]
    ref = [r for r in rows if r["is_reference"]]
    assert len(own) == 10
    assert len(ref) == 30

    summary = ds.summarize(rows)
    assert summary["own_rows"] == 10
    assert summary["reference_rows"] == 30


def test_reference_labels_stay_channel_relative(isolated_root):
    """A 500k-view video on a 500k-view channel is a 1.0, not a triumph."""
    write_cache(isolated_root, [make_account("mine", count=10)])
    install_reference([_ref_account()])

    rows = ds.build_rows(now=NOW)
    ref = [r for r in rows if r["is_reference"]]
    multiples = [
        r["targets"]["normalized_views"] for r in ref
        if r["targets"]["normalized_views"] is not None
        and r["normalization_basis"] == "channel"
    ]
    assert multiples
    assert all(0.2 < m < 5.0 for m in multiples)


def test_a_winners_only_import_would_flatten_the_label(isolated_root):
    """Why full history is imported rather than "the best videos".

    Keep only the top decile of a channel and the label it is normalized
    against becomes the top decile's own median: everything lands at ~1.0
    and there is nothing left to learn.
    """
    account = make_account("mine", count=40)
    for index, video in enumerate(account["videos"]):
        video["view_count"] = 100 + index * 100  # 100 .. 4000
    winners = sorted(account["videos"], key=lambda v: v["view_count"],
                     reverse=True)[:12]
    write_cache(isolated_root, [{**account, "videos": winners}])

    rows = ds.build_rows(now=NOW)
    multiples = [
        r["targets"]["normalized_views"] for r in rows
        if r["targets"]["normalized_views"] is not None
    ]
    spread = max(multiples) - min(multiples)
    assert spread < 0.7, f"winners-only spread was {spread:.2f}"


def test_a_full_history_keeps_the_label_informative(isolated_root):
    account = make_account("mine", count=40)
    for index, video in enumerate(account["videos"]):
        video["view_count"] = 100 + index * 100
    write_cache(isolated_root, [account])

    rows = ds.build_rows(now=NOW)
    multiples = [
        r["targets"]["normalized_views"] for r in rows
        if r["targets"]["normalized_views"] is not None
    ]
    assert max(multiples) - min(multiples) > 1.0
