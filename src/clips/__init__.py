"""Clips: learn what wins in short form, then cut it out of a long video.

Two halves that meet in the scorer:

  - `trends` reads the channel history this installation already has —
    the reference channels Brain imported, plus our own — and works out
    what separates the top decile of shorts from the rest. Not a trending
    feed: a feed says what is popular, history says what *worked*, with
    the losers still in the data so the difference means something.

  - the pipeline takes a source video, transcribes it to timed words,
    cuts it into candidates that never break a sentence, scores them
    against that profile, and renders the best few as 1080x1920 clips
    with burned-in subtitles.

Clips are produced as drafts for review. Only an explicit action through
`clips.upload` uploads a chosen clip to YouTube using the channel's Firefox
profile, reusing the existing Shorts uploader.

Usage:

    import clips

    clips.trend_profile()                    # what wins, and how it knows
    clips.make("https://...", count=3,
               rights_note="my own channel") # cut and render
    clips.catalogue(status="draft")          # what is waiting for review

The command line is `python scripts/clips.py` — see `docs/Clips.md`.
"""

from __future__ import annotations

from typing import Any, Callable

from . import config, ingest, pipeline, render, scoring, segments, store, transcribe, trends
from .pipeline import ClipError

__all__ = [
    "ClipError",
    "catalogue",
    "config",
    "cut",
    "decisions",
    "full",
    "ingest",
    "make",
    "pipeline",
    "preview",
    "render",
    "review",
    "scoring",
    "segments",
    "store",
    "transcribe",
    "trend_brief",
    "trend_profile",
    "trends",
]


def trend_profile(refresh: bool = False) -> dict[str, Any]:
    """What separates winning shorts from the rest, with its sample sizes."""
    return trends.profile(refresh=refresh)


def trend_brief(refresh: bool = False) -> str:
    """The trend profile as a few lines fit for a prompt."""
    return trends.prompt_brief(trends.profile(refresh=refresh))


def make(
    target: str,
    count: int | None = None,
    rights_note: str = "",
    on_status: Callable[[str], None] | None = None,
    **kwargs,
) -> dict[str, Any]:
    """Cut and render clips from a URL or a local file."""
    return pipeline.run(
        target,
        count=count,
        rights_note=rights_note,
        on_status=on_status or (lambda message: None),
        **kwargs,
    )


def cut(
    target: str,
    spans: list[str],
    rights_note: str = "",
    on_status: Callable[[str], None] | None = None,
    **kwargs,
) -> dict[str, Any]:
    """Render hand-picked spans ("1:32-2:03") with the clip renderer."""
    return pipeline.cut(
        target,
        spans,
        rights_note=rights_note,
        on_status=on_status or (lambda message: None),
        **kwargs,
    )


def preview(target: str, count: int | None = None, **kwargs) -> dict[str, Any]:
    """Choose clips and report the picks without encoding anything."""
    return make(target, count=count, dry_run=True, **kwargs)


def full(
    target: str,
    clip_seconds: float = 60,
    batch_size: int = 0,
    resume: bool = True,
    rights_note: str = "",
    on_status: Callable[[str], None] | None = None,
    **kwargs,
) -> dict[str, Any]:
    """Cover an entire video in consecutive parts, resuming what is missing."""
    return pipeline.full(
        target, clip_seconds=clip_seconds, batch_size=batch_size, resume=resume,
        rights_note=rights_note, on_status=on_status or (lambda message: None), **kwargs,
    )


def catalogue(status: str = "", source_id: str = "") -> list[dict[str, Any]]:
    """Rendered clips, newest first."""
    return store.listing(status=status, source_id=source_id)


def review(clip_id: str, status: str) -> dict[str, Any] | None:
    """Move a clip to `approved`, `rejected` or `published`."""
    return store.set_status(clip_id, status)


def decisions(limit: int = 50) -> list[dict[str, Any]]:
    """The decision log: what was picked, and what lost to it."""
    return store.read_decisions(limit=limit)
