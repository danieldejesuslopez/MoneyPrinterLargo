"""
Clips — FastAPI router.

REST + SSE over `src/clips/`: the clip catalogue, the learned trend
profile, cached sources, review actions, and a live job that runs
`scripts/clips.py` (auto picks or hand-picked spans) through the same job
system as video generation, so a clip run also shows up in the background
jobs panel and in Operations.

Creation produces drafts. Uploads start only from an explicit upload action.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Literal, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

API_DIR = Path(__file__).resolve().parent
ROOT_DIR = API_DIR.parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import clips  # noqa: E402
from clips import pipeline as clips_pipeline  # noqa: E402
from clips import store as clips_store  # noqa: E402
from clips import trends as clips_trends  # noqa: E402
from clips import config as clips_config  # noqa: E402
from clips import upload as clips_upload  # noqa: E402
from clips import continuation  # noqa: E402

router = APIRouter(prefix="/api/clips", tags=["clips"])

# The job system lives in main.py, which imports this module before it
# defines its job helpers. main.py hands them over with `bind_jobs()` once
# they exist, instead of this module importing main (a circular import).
_spawn_job: Optional[Callable[..., Any]] = None
_stream_job: Optional[Callable[..., Any]] = None
_UPLOAD_JOBS: dict[str, Any] = {}
_CLIP_UPLOAD_JOBS: dict[str, str] = {}
_UPLOAD_JOBS_LOCK = threading.Lock()

LAYOUTS = ("blur", "crop", "facecam")

#: Rights note recorded for URL runs started from the webapp, which does not
#: ask for one.
DEFAULT_RIGHTS = "sin nota (webapp)"
STATUSES = clips_store.STATUSES


def bind_jobs(spawn: Callable[..., Any], stream: Callable[..., Any]) -> None:
    global _spawn_job, _stream_job
    _spawn_job, _stream_job = spawn, stream


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clips_dir() -> Path:
    return Path(clips_config.clips_dir()).resolve()


def _clip_path(record: dict) -> Optional[Path]:
    """The clip's file, only if it is really inside `.mp/clips/`."""
    raw = str(record.get("path") or "")
    if not raw:
        return None
    path = Path(raw).resolve()
    try:
        path.relative_to(_clips_dir())
    except ValueError:
        return None
    return path if path.is_file() else None


def _find(clip_id: str) -> dict:
    for record in clips_store.load()["clips"]:
        if record.get("id") == clip_id:
            return record
    raise HTTPException(404, "Clip not found")


def _clip_out(record: dict) -> dict:
    path = _clip_path(record)
    return {
        "id": record.get("id"),
        "title": record.get("title") or "",
        "status": record.get("status") or "draft",
        "created_at": record.get("created_at") or "",
        "start": record.get("start"),
        "end": record.get("end"),
        "duration": record.get("duration"),
        "score": record.get("score"),
        "picked_by": record.get("picked_by") or "auto",
        "layout": record.get("layout") or "",
        "words_from": record.get("words_from") or "",
        "transcript": record.get("transcript") or "",
        "source_id": record.get("source_id") or "",
        "source_title": record.get("source_title") or "",
        "source_url": record.get("source_url") or "",
        "source_target": record.get("source_url") or record.get("source_path") or "",
        "source_uploader": record.get("source_uploader") or "",
        "rights_note": record.get("rights_note") or "",
        "probe": record.get("probe") or {},
        "youtube_upload": record.get("youtube_upload") or {},
        "sequence_index": record.get("sequence_index"),
        "sequence_total": record.get("sequence_total"),
        "sequence_id": record.get("sequence_id"),
        "clip_seconds": record.get("clip_seconds"),
        "available": path is not None,
        "size_bytes": path.stat().st_size if path else 0,
    }


def _is_url(target: str) -> bool:
    return bool(re.match(r"^https?://", (target or "").strip(), re.IGNORECASE))


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------


@router.get("")
def list_clips(status: str = "", source_id: str = ""):
    if status and status not in STATUSES:
        raise HTTPException(400, f"status must be one of {', '.join(STATUSES)}")
    clips_upload.reconcile_uploads()
    return [_clip_out(r) for r in clips_store.listing(status=status, source_id=source_id)]


class ClipStatusIn(BaseModel):
    status: str


@router.patch("/{clip_id}")
def set_clip_status(clip_id: str, payload: ClipStatusIn):
    if payload.status not in STATUSES:
        raise HTTPException(400, f"status must be one of {', '.join(STATUSES)}")
    if (_find(clip_id).get("youtube_upload") or {}).get("status") in ("uploading", "pending_confirmation"):
        raise HTTPException(409, "Termina o resuelve la subida antes de cambiar este clip")
    updated = clips_store.set_status(clip_id, payload.status)
    if not updated:
        raise HTTPException(404, "Clip not found")
    return _clip_out(updated)


@router.delete("/{clip_id}")
def delete_clip(clip_id: str):
    """Remove a clip from the catalogue and delete its video and subtitles.

    Only files inside `.mp/clips/` are ever deleted; the source video it was
    cut from is left alone so other clips can still be cut from it.
    """
    record = _find(clip_id)
    if (record.get("youtube_upload") or {}).get("status") in ("uploading", "pending_confirmation"):
        raise HTTPException(409, "Termina o resuelve la subida antes de borrar este clip")
    clips_store.remove(clip_id)
    deleted = []
    path = _clip_path(record)
    if path:
        for sibling in (path, path.with_suffix(".ass")):
            try:
                if sibling.is_file():
                    sibling.unlink()
                    deleted.append(sibling.name)
            except OSError:
                pass
    return {"ok": True, "deleted": deleted}


@router.get("/{clip_id}/raw")
def clip_raw(clip_id: str):
    path = _clip_path(_find(clip_id))
    if not path:
        raise HTTPException(404, "Clip file missing")
    return FileResponse(str(path), media_type="video/mp4", filename=path.name)


class ClipUploadIn(BaseModel):
    channel_id: str = Field(min_length=1)
    title: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=5000)


class ClipUploadResolutionIn(BaseModel):
    action: Literal["published", "retry"]
    url: str = Field(default="", max_length=2048)


@router.post("/{clip_id}/upload-resolution")
def resolve_clip_upload(clip_id: str, payload: ClipUploadResolutionIn):
    """Record the user's Studio verification without starting another upload."""
    _find(clip_id)
    try:
        updated = clips_upload.resolve_upload(clip_id, payload.action, payload.url)
    except clips_upload.UploadError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _clip_out(updated)


@router.post("/{clip_id}/upload")
def upload_clip(clip_id: str, payload: ClipUploadIn):
    """Start one explicit upload. Reconnecting to its logs never re-uploads."""
    if _spawn_job is None or _stream_job is None:
        raise HTTPException(503, "Job system not ready")
    _find(clip_id)
    with _UPLOAD_JOBS_LOCK:
        # Firefox's shared uploader starts a new session. Serialize clip
        # uploads so a second click cannot interrupt the first session.
        if any(getattr(job, "status", "running") == "running" for job in _UPLOAD_JOBS.values()):
            raise HTTPException(409, "Ya hay un clip subiendo a YouTube; espera a que termine")
        try:
            review = clips_upload.validate_upload(
                clip_id, payload.channel_id.strip(),
                title=payload.title, description=payload.description,
            )
        except clips_upload.UploadError as exc:
            raise HTTPException(400, str(exc)) from exc
        args = [
            "clips", "upload", clip_id, "--channel-id", payload.channel_id.strip(),
            "--title", review["title"], "--description", review["description"],
        ]
        label = str(review["channel"].get("nickname") or payload.channel_id)
        job = _spawn_job(args, title=f"Subiendo clip a YouTube · {label} · {review['title'][:60]}")
        _UPLOAD_JOBS[job.id] = job
        _CLIP_UPLOAD_JOBS[clip_id] = job.id
        # Keep a bounded set of recent streams available for reconnection.
        for job_id in list(_UPLOAD_JOBS):
            if len(_UPLOAD_JOBS) <= 128:
                break
            if getattr(_UPLOAD_JOBS[job_id], "status", "running") != "running":
                del _UPLOAD_JOBS[job_id]
                for old_clip, old_job in list(_CLIP_UPLOAD_JOBS.items()):
                    if old_job == job_id:
                        del _CLIP_UPLOAD_JOBS[old_clip]
    return {"job_id": job.id, "stream_url": f"/api/clips/uploads/{job.id}/events"}


@router.get("/uploads/{job_id}/events")
async def upload_events(job_id: str):
    if _stream_job is None:
        raise HTTPException(503, "Job system not ready")
    with _UPLOAD_JOBS_LOCK:
        job = _UPLOAD_JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "Upload job not found")
    return EventSourceResponse(_stream_job(job))


# ---------------------------------------------------------------------------
# What the module has learned, and what it can cut from
# ---------------------------------------------------------------------------


@router.get("/meta/upload-channels")
def upload_channels():
    from cache import get_accounts

    channels = []
    for channel in get_accounts("youtube"):
        try:
            profile = clips_upload.resolve_profile(channel)
        except (clips_upload.UploadError, OSError, ValueError):
            profile = ""
        channels.append({
            "id": channel.get("id"),
            "nickname": channel.get("nickname") or channel.get("id") or "YouTube",
            "firefox_profile": profile,
            "profile_ready": bool(profile and os.path.isdir(profile)),
        })
    return channels


@router.get("/meta/trends")
def trends(refresh: bool = False):
    profile = clips_trends.profile(refresh=refresh)
    low, high = clips_trends.duration_window(profile)
    return {
        "profile": profile,
        "brief": clips_trends.prompt_brief(profile),
        "window": [round(low, 1), round(high, 1)],
    }


@router.get("/meta/sources")
def sources():
    """Videos already downloaded or transcribed, newest first."""
    root = Path(clips_config.sources_dir())
    records = clips_store.listing()
    out = []
    for manifest in root.glob("*/source.json"):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        transcript = manifest.parent / "transcript.json"
        if not data and not transcript.exists():
            continue
        out.append({
            "id": data.get("id") or manifest.parent.name,
            "title": data.get("title") or manifest.parent.name,
            "url": data.get("url") or "",
            "uploader": data.get("uploader") or "",
            "duration_seconds": data.get("duration_seconds") or 0,
            "captions_kind": data.get("captions_kind") or "",
            "rights_note": data.get("rights_note") or "",
            "fetched_at": data.get("fetched_at") or "",
            "target": data.get("url") or data.get("path") or "",
        })
    # Local sources used by earlier cutting flows may not have a cached
    # manifest. New records retain their exact source path for continuation.
    known = {source["id"] for source in out}
    for record in records:
        source_id = record.get("source_id")
        target = record.get("source_url") or record.get("source_path")
        if not source_id or source_id in known or not target:
            continue
        known.add(source_id)
        out.append({
            "id": source_id,
            "title": record.get("source_title") or source_id,
            "url": record.get("source_url") or "",
            "uploader": record.get("source_uploader") or "",
            "duration_seconds": record.get("source_duration_seconds") or 0,
            "captions_kind": "", "rights_note": record.get("rights_note") or "",
            "fetched_at": record.get("created_at") or "", "target": target,
        })
    for source in out:
        source["sequence"] = _source_sequence(root / source["id"], source["id"], records)
    out.sort(key=lambda s: s["fetched_at"], reverse=True)
    return out


def _source_sequence(directory: Path, source_id: str, records: list[dict]) -> dict | None:
    """Progress against the saved boundaries, including holes and missing files."""
    try:
        plans = sorted(directory.glob("sequence-*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    except OSError:
        return None
    for path in plans:
        try:
            plan = json.loads(path.read_text(encoding="utf-8"))
            if plan.get("source_id") != source_id:
                continue
            identity = plan.get("identity")
            if isinstance(identity, dict):
                media = Path(identity["path"])
                current = media.stat()
                if current.st_size != identity["size"] or current.st_mtime_ns != identity["mtime_ns"]:
                    continue
            sequence_id = plan.get("sequence_id")
            signature = plan.get("source_signature")
            eligible = [record for record in records if (
                (sequence_id and record.get("sequence_id") == sequence_id)
                or (not plan.get("isolated_sequence", False) and (
                    (signature and record.get("source_signature") == signature)
                    or (plan.get("accept_legacy", True) and not record.get("sequence_id"))
                ))
            )]
            progress = continuation.coverage_plan(plan["spans"], eligible, source_id)
            return {
                key: progress[key] for key in (
                    "total_parts", "completed_parts", "remaining_parts", "next_start", "complete"
                )
            } | {"clip_seconds": plan["clip_seconds"], "sequence_id": sequence_id}
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            continue
    return None


@router.get("/meta/decisions")
def decisions(limit: int = 20):
    return clips_store.read_decisions(limit=max(1, min(limit, 200)))


# ---------------------------------------------------------------------------
# Live run
# ---------------------------------------------------------------------------


@router.get("/run")
async def run_clips(
    target: str,
    mode: str = "auto",
    spans: str = "",
    titles: str = "",
    count: int = 3,
    rights: str = "",
    language: str = "",
    layout: str = "",
    facecam: str = "",
    subtitles: bool = True,
    whisper: bool = False,
    use_llm: bool = True,
    clip_seconds: float = 60.0,
    batch_size: int = 0,
    resume: bool = True,
):
    """Run the clips CLI as a background job and stream its progress (SSE).

    mode=auto   — the module picks `count` clips itself (`clips.py run`)
    mode=cut    — render hand-picked `spans` ("1:32-1:58;8:05-8:50"), with
                  optional `titles` separated by "|" (`clips.py cut`)
    mode=full   — consecutive parts covering the whole source; resumes
                  completed parts and accepts an optional batch limit
    """
    if _spawn_job is None or _stream_job is None:
        raise HTTPException(503, "Job system not ready")
    target = (target or "").strip()
    if not target:
        raise HTTPException(400, "Pega una URL o la ruta de un vídeo")
    if _is_url(target) and not rights.strip():
        # The page has no rights field; the CLI still wants a note for a URL,
        # so record that the clip came from the webapp without one.
        rights = DEFAULT_RIGHTS
    if not _is_url(target) and not os.path.isfile(target):
        raise HTTPException(400, f"No existe el archivo: {target}")
    if mode not in ("auto", "cut", "full"):
        raise HTTPException(400, "mode must be auto, cut or full")
    if mode == "full" and (
        not math.isfinite(clip_seconds) or not 5 <= clip_seconds <= 180 or batch_size < 0
    ):
        raise HTTPException(400, "La duración por parte debe estar entre 5 y 180 segundos y el lote debe ser 0 o mayor")
    if layout and layout not in LAYOUTS:
        raise HTTPException(400, f"layout must be one of {', '.join(LAYOUTS)}")
    if layout == "facecam" and facecam and clips.render.parse_box(facecam) is None:
        raise HTTPException(400, 'facecam must look like "898,11,360,200"')

    args = ["clips"]
    if mode == "cut":
        span_list = [s.strip() for s in re.split(r"[;,\n]+", spans) if s.strip()]
        if not span_list:
            raise HTTPException(400, "Añade al menos un tramo, por ejemplo 1:32-1:58")
        for span in span_list:
            try:
                clips_pipeline.parse_span(span)
            except clips.ClipError as exc:
                raise HTTPException(400, str(exc)) from exc
        args += ["cut", target]
        for span in span_list:
            args += ["--at", span]
        # One --title per span, in order, empty where a span has none: the
        # CLI pairs titles with spans by position and falls back to the
        # clip's opening words for an empty one. Dropping the empty ones
        # would shift every later title onto the wrong clip.
        title_list = [t.strip() for t in titles.split("|")] if titles else []
        if any(title_list):
            for index in range(len(span_list)):
                args += ["--title", title_list[index] if index < len(title_list) else ""]
    elif mode == "full":
        args += ["full", target, "--clip-seconds", str(clip_seconds), "--batch-size", str(batch_size)]
        if not resume:
            args.append("--no-resume")
    else:
        args += ["run", target, "--count", str(max(1, min(int(count), 10)))]
        if not use_llm:
            args.append("--no-llm")

    if rights.strip():
        args += ["--rights", rights.strip()]
    if language.strip():
        args += ["--language", language.strip()]
    if layout:
        args += ["--layout", layout]
    if facecam.strip():
        args += ["--facecam", facecam.strip()]
    if not subtitles:
        args.append("--no-subtitles")
    if whisper:
        args.append("--whisper")

    label = target if len(target) <= 60 else target[:57] + "..."
    mode_label = {"auto": "auto", "cut": "tramos", "full": "video completo"}[mode]
    title = f"Clips — {mode_label} · {label}"
    # No channel_id/kind: those switch on "Subir a YouTube" in the jobs panel,
    # and a clip is a draft for review, never auto-uploaded from here.
    job = _spawn_job(args, title=title)
    return EventSourceResponse(_stream_job(job))
