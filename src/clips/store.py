"""Where clips and the reasoning behind them are kept.

Two files, with different jobs:

  - `.mp/clips/clips.json` is the catalogue: one record per rendered
    clip, its source, its span, its scores and its review status. This is
    what a CLI or the webapp lists.

  - `.mp/clips/decisions.jsonl` is append-only and records what *lost*.
    A catalogue of the winners cannot answer "is the scorer any good?",
    because the clips that were rejected are exactly the evidence needed.
    `src/brain/experiments/decisions.py` keeps the same kind of log for
    the same reason: it is what makes the picks measurable later, once
    published clips have view counts.

Clips are never auto-published. A record starts at `draft` and only a
person moves it on.
"""

from __future__ import annotations

import datetime as _dt
import contextlib
import json
import os
import time
import uuid
from typing import Any

from . import config as clips_config


CATALOGUE_FILE = "clips.json"

STATUS_DRAFT = "draft"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_PUBLISHED = "published"
STATUSES = (STATUS_DRAFT, STATUS_APPROVED, STATUS_REJECTED, STATUS_PUBLISHED)


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def catalogue_path() -> str:
    return clips_config.clips_file(CATALOGUE_FILE)


@contextlib.contextmanager
def file_lock(path: str, timeout: float = 15.0):
    """An OS-owned lock, released even if an upload subprocess crashes."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise BlockingIOError("El archivo está ocupado por otra tarea.") from exc
                time.sleep(0.05)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def load() -> dict[str, Any]:
    try:
        with open(catalogue_path(), "r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, ValueError):
        return {"clips": []}
    if not isinstance(data, dict):
        return {"clips": []}
    clips = data.get("clips")
    return {"clips": [c for c in clips if isinstance(c, dict)]} if isinstance(clips, list) else {"clips": []}


def save(data: dict[str, Any]) -> str:
    path = catalogue_path()
    tmp = path + f".{uuid.uuid4().hex}.tmp"
    with open(tmp, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def new_clip_id() -> str:
    return uuid.uuid4().hex[:12]


def add(record: dict[str, Any]) -> dict[str, Any]:
    """Put one rendered clip in the catalogue."""
    record.setdefault("id", new_clip_id())
    record.setdefault("status", STATUS_DRAFT)
    record.setdefault("created_at", _now())
    with file_lock(catalogue_path() + ".lock"):
        data = load()
        data["clips"].append(record)
        save(data)
    return record


def update(clip_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
    """Merge fields into the latest row without losing other clips or updates."""
    with file_lock(catalogue_path() + ".lock"):
        data = load()
        for clip in data["clips"]:
            if clip.get("id") == clip_id:
                clip.update(changes)
                save(data)
                return clip
    return None


def remove(clip_id: str) -> dict[str, Any] | None:
    """Remove one row atomically, leaving other clips' upload progress intact."""
    with file_lock(catalogue_path() + ".lock"):
        data = load()
        for index, clip in enumerate(data["clips"]):
            if clip.get("id") == clip_id:
                data["clips"].pop(index)
                save(data)
                return clip
    return None


def set_status(clip_id: str, status: str) -> dict[str, Any] | None:
    """Move a clip's review status. Returns the updated record."""
    if status not in STATUSES:
        raise ValueError(f"unknown status '{status}'; expected one of {STATUSES}")
    return update(clip_id, {"status": status, "status_changed_at": _now()})


def listing(status: str = "", source_id: str = "") -> list[dict[str, Any]]:
    """Catalogue rows, newest first, optionally filtered."""
    rows = load()["clips"]
    if status:
        rows = [r for r in rows if r.get("status") == status]
    if source_id:
        rows = [r for r in rows if r.get("source_id") == source_id]
    return sorted(rows, key=lambda r: str(r.get("created_at") or ""), reverse=True)


def log_decision(entry: dict[str, Any]) -> None:
    """Append one run's reasoning: what was picked, and what was not.

    Append-only and best-effort. A failure to write the log must never
    lose clips that were already rendered.
    """
    entry = {"at": _now(), **entry}
    try:
        with open(
            clips_config.clips_file(clips_config.DECISIONS_FILE), "a", encoding="utf-8"
        ) as file:
            file.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def read_decisions(limit: int = 50) -> list[dict[str, Any]]:
    """The most recent decision-log entries, newest last."""
    path = clips_config.clips_file(clips_config.DECISIONS_FILE)
    try:
        with open(path, "r", encoding="utf-8") as file:
            lines = file.readlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            out.append(parsed)
    return out
