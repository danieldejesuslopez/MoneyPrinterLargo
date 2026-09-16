"""Shared fixtures for the Brain test suite.

Every test runs against a throwaway project root, so nothing here can read
or write the real `.mp/` or `models/` directories. `brain.config.root_dir`
is the single place all paths are derived from, which is what makes that
redirection a one-line fixture rather than a pile of monkeypatches.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

NOW = dt.datetime(2026, 6, 1, 12, 0, tzinfo=dt.timezone.utc)


@pytest.fixture(autouse=True)
def isolated_root(tmp_path, monkeypatch):
    """Point every Brain path at a temporary project root."""
    from brain import config as brain_config

    (tmp_path / ".mp").mkdir()
    (tmp_path / "models").mkdir()
    monkeypatch.setattr(brain_config, "root_dir", lambda: str(tmp_path))

    # image_stats memoizes by path; a stale entry would leak between tests.
    from brain.features import thumbnail

    thumbnail.image_stats.cache_clear()
    return tmp_path


def write_cache(root: Path, accounts: list[dict]) -> Path:
    path = root / ".mp" / "youtube.json"
    path.write_text(
        json.dumps({"accounts": accounts}, ensure_ascii=False), encoding="utf-8"
    )
    return path


def make_video(
    index: int,
    *,
    strong: bool,
    published: dt.datetime,
    views: float,
    account_suffix: str = "",
    **overrides,
) -> dict:
    """One cache entry, in exactly the shape the pipeline writes."""
    if strong:
        title = f"{index + 3} datos que nadie te conto sobre el cometa Halley"
        subject = f"El misterio del cometa {index}"
    else:
        title = f"Cosas del espacio parte {index}"
        subject = f"Espacio general {index}"

    video = {
        "title": title,
        "description": "Un video sobre el espacio.",
        "subject": subject,
        "url": f"https://youtu.be/{_fake_id(index, account_suffix)}",
        "date": published.strftime("%Y-%m-%d %H:%M:%S"),
        "is_short": True,
        "view_count": int(views),
        "like_count": int(views * 0.04),
        "comment_count": int(views * 0.004),
        "avg_percentage_viewed": 55.0 if strong else 35.0,
        "duration_seconds": 48.0,
        "stats_synced_at": (published + dt.timedelta(days=30)).strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        "retention_score": 8.4 if strong else 6.1,
        "hook_score": 8.0 if strong else 6.5,
    }
    video.update(overrides)
    return video


def _fake_id(index: int, suffix: str = "") -> str:
    """An 11-character YouTube-shaped id, unique per (index, account)."""
    base = f"vid{suffix}{index:04d}"
    return (base + "xxxxxxxxxxx")[:11]


def make_account(
    account_id: str = "acc-1",
    count: int = 60,
    now: dt.datetime = NOW,
    strong_every: int = 2,
    account_suffix: str = "",
) -> dict:
    """A channel whose over-performers are exactly the numbered titles.

    The signal is deliberately blunt: videos with a number and a curiosity
    phrase get 3x the views. A model that cannot find that is broken, and a
    model that finds it proves the whole path from cache to weights works.
    """
    videos = []
    for index in range(count):
        published = now - dt.timedelta(days=(count - index) * 2 + 40)
        strong = index % strong_every == 0
        views = 3000.0 + index * 5 if strong else 1000.0 + index * 5
        videos.append(
            make_video(
                index,
                strong=strong,
                published=published,
                views=views,
                account_suffix=account_suffix,
            )
        )
    return {
        "id": account_id,
        "nickname": "Cosmos Test",
        "niche": "cosmos",
        "language": "es",
        "videos": videos,
    }


@pytest.fixture
def channel(isolated_root):
    """A 60-video channel already written to the temporary cache."""
    from brain.data import collector

    write_cache(isolated_root, [make_account()])
    found = collector.find_channel("acc-1", now=NOW)
    assert found is not None
    return found


@pytest.fixture
def dataset_rows(channel):
    from brain.data import dataset as ds

    return ds.build_rows([channel], now=NOW)
