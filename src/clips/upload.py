"""Upload a reviewed clip through the existing YouTube Firefox flow.

Rendering never publishes. An explicit upload uses the exact catalogue
file and the selected channel's saved Firefox profile, with the global
profile as fallback. Local clips stay available after uploading.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import re
from contextlib import ExitStack
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from . import config as clips_config
from . import store


class UploadError(RuntimeError):
    """A clip upload cannot proceed, with a message suitable for the UI."""


def get_channel(channel_id: str) -> dict[str, Any]:
    """Resolve an existing configured channel without opening a browser."""
    channel_id = str(channel_id or "").strip()
    if not channel_id:
        raise UploadError("Elige un canal de YouTube para subir el clip.")
    path = Path(clips_config.root_dir()) / ".mp" / "youtube.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UploadError("No se pudo leer la lista de canales de YouTube.") from exc
    accounts = data.get("accounts", []) if isinstance(data, dict) else []
    for channel in accounts if isinstance(accounts, list) else []:
        if isinstance(channel, dict) and str(channel.get("id")) == channel_id:
            return dict(channel)
    raise UploadError("El canal de YouTube seleccionado no existe.")


def resolve_profile(channel: dict[str, Any]) -> str:
    """The channel's Firefox profile, falling back only when it is empty."""
    if channel.get("automation_profile_verified") is False:
        raise UploadError(str(channel.get("upload_disabled_reason") or "Confirma el perfil de Firefox de este canal antes de subir"))
    raw = str(channel.get("firefox_profile") or "").strip()
    if not raw:
        try:
            settings = json.loads(
                (Path(clips_config.root_dir()) / "config.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise UploadError("Configura un perfil de Firefox para el canal o en Configuración.") from exc
        raw = str(settings.get("firefox_profile") or "").strip() if isinstance(settings, dict) else ""
    if not raw:
        raise UploadError("Configura un perfil de Firefox para el canal o en Configuración.")
    profile = Path(raw).expanduser()
    if not profile.is_absolute():
        profile = Path(clips_config.root_dir()) / profile
    if not profile.is_dir():
        raise UploadError("El perfil de Firefox configurado no existe o no es una carpeta.")
    return str(profile.resolve())


def _clip(clip_id: str) -> dict[str, Any]:
    record = next((row for row in store.load()["clips"] if row.get("id") == clip_id), None)
    if record is None:
        raise UploadError("El clip no existe.")
    return record


def _global_lock_path() -> str:
    return str(Path(clips_config.clips_dir()) / ".youtube-upload.lock")


def reconcile_uploads() -> None:
    """Turn interrupted worker records into uploads requiring Studio review.

    A live worker owns the global Firefox lock for its entire transfer.
    The catalogue's durable state must never imply an automatic retry
    merely because its subprocess exited or the backend restarted.
    """
    try:
        with store.file_lock(_global_lock_path(), timeout=0):
            # Reload after obtaining the lock; a worker may have finished
            # while a caller was reading the catalogue.
            for clip in store.load()["clips"]:
                state = clip.get("youtube_upload")
                if not isinstance(state, dict) or state.get("status") != "uploading":
                    continue
                state = {
                    **state,
                    "status": "pending_confirmation",
                    "error": "Se interrumpió el trabajo de subida. Revisa YouTube Studio antes de confirmar el resultado o reintentar.",
                    "finished_at": store._now(),
                }
                store.update(clip["id"], {"youtube_upload": state})
    except BlockingIOError:
        # Another worker is active. Its state remains authoritative.
        return


def _check_pending_uploads() -> None:
    for clip in store.load()["clips"]:
        state = clip.get("youtube_upload")
        if isinstance(state, dict) and state.get("status") == "pending_confirmation":
            raise UploadError(
                "Hay una subida de clips pendiente de confirmación. Revisa YouTube Studio y confirma "
                "su resultado antes de iniciar otra subida, para no interrumpir Firefox."
            )


def _check_status(clip: dict[str, Any]) -> None:
    if clip.get("status") == store.STATUS_REJECTED:
        raise UploadError("El clip está rechazado. Apruébalo antes de subirlo.")
    previous = clip.get("youtube_upload") or {}
    state = previous.get("status") if isinstance(previous, dict) else ""
    if clip.get("status") == store.STATUS_PUBLISHED or state == "uploaded":
        raise UploadError("Este clip ya se subió a YouTube.")
    if state == "uploading":
        raise UploadError("Este clip ya tiene una subida en curso. Revisa el trabajo de subida.")
    if state == "pending_confirmation":
        raise UploadError("La subida necesita verificación en YouTube Studio antes de volver a intentarla.")


def _default_description(clip: dict[str, Any]) -> str:
    text = str(clip.get("transcript") or "").strip()
    source = str(clip.get("source_title") or "").strip()
    url = str(clip.get("source_url") or "").strip()
    attribution = "\n".join(part for part in (f"Fuente: {source}" if source else "", url) if part)
    # Keep the source and hashtag when an unusually long transcript is trimmed.
    ending = "\n\n".join(part for part in (attribution, "#Shorts") if part)
    budget = max(0, 5000 - len(ending) - 2)
    return "\n\n".join(part for part in (text[:budget].rstrip(), ending) if part)


def validate_upload(
    clip_id: str,
    channel_id: str,
    title: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Return concrete upload data after validation; perform no browser work."""
    reconcile_uploads()
    clip = _clip(clip_id)
    _check_status(clip)
    _check_pending_uploads()
    raw = str(clip.get("path") or "").strip()
    if not raw:
        raise UploadError("El archivo del clip no existe.")
    path = Path(raw).resolve()
    root = Path(clips_config.clips_dir()).resolve()
    if not path.is_relative_to(root):
        raise UploadError("El archivo del clip debe estar dentro de la carpeta de clips.")
    if not path.is_file():
        raise UploadError("El archivo del clip no existe.")
    channel = get_channel(channel_id)
    profile = resolve_profile(channel)
    clean_title = re.sub(r"\s+", " ", str(clip.get("title") or "") if title is None else title).strip()
    clean_description = _default_description(clip) if description is None else description.strip()
    if not clean_title:
        raise UploadError("Escribe un título para el clip.")
    if len(clean_title) > 100:
        if title is not None:
            raise UploadError("El título de YouTube admite hasta 100 caracteres.")
        clean_title = clean_title[:100].rstrip()
    if not clean_description:
        raise UploadError("Escribe una descripción para el clip.")
    if len(clean_description) > 5000:
        raise UploadError("La descripción de YouTube admite hasta 5000 caracteres.")
    return {
        "clip": clip,
        "channel": channel,
        "profile": profile,
        "path": str(path),
        "title": clean_title,
        "description": clean_description,
    }


def _youtube_class():
    """Keep Selenium and the generation stack out of validation/API startup."""
    return importlib.import_module("classes.YouTube").YouTube


def _video_url(value: str) -> str:
    """Reject the Studio placeholder and URLs that do not name a video."""
    value = str(value or "").strip()
    try:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https":
            return ""
        video_id = ""
        if host in ("youtube.com", "www.youtube.com", "m.youtube.com"):
            if parsed.path == "/watch":
                video_id = (parse_qs(parsed.query).get("v") or [""])[0]
            elif parsed.path.startswith(("/shorts/", "/live/")):
                video_id = parsed.path.split("/")[2]
        elif host == "youtu.be":
            video_id = parsed.path.strip("/")
        return value if re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id) else ""
    except (ValueError, IndexError):
        return ""


def resolve_upload(clip_id: str, action: str, url: str = "") -> dict[str, Any]:
    """Record the result the user checked in Studio; never start an upload."""
    if action not in ("published", "retry"):
        raise UploadError("Elige confirmar la publicación o permitir un reintento después de revisar YouTube Studio.")
    reconcile_uploads()
    try:
        with store.file_lock(_global_lock_path(), timeout=0):
            clip = _clip(clip_id)
            previous = clip.get("youtube_upload")
            if not isinstance(previous, dict) or previous.get("status") != "pending_confirmation":
                raise UploadError("Solo se puede resolver una subida pendiente de confirmación en YouTube Studio.")
            state = dict(previous)
            now = store._now()
            changes: dict[str, Any] = {"status_changed_at": now}
            if action == "published":
                confirmed_url = _video_url(url)
                if not confirmed_url:
                    raise UploadError("Pega la URL válida del video de YouTube que verificaste en Studio.")
                state.update(
                    status="uploaded", url=confirmed_url, error="", finished_at=now,
                    confirmed_manually=True,
                )
                changes["status"] = store.STATUS_PUBLISHED
            else:
                # This explicit action means the user checked Studio and
                # chose to make a later retry possible. It does not open
                # Firefox or begin another transfer now.
                state.update(status="failed", error="", finished_at=now)
                changes["status"] = store.STATUS_APPROVED
            changes["youtube_upload"] = state
            updated = store.update(clip_id, changes)
            if updated is None:
                raise UploadError("El clip ya no existe en el catálogo.")
            return updated
    except BlockingIOError as exc:
        raise UploadError("Hay una subida de clips en curso. Espera a que termine antes de resolver el resultado en Studio.") from exc


def upload_clip(
    clip_id: str,
    channel_id: str,
    title: str | None = None,
    description: str | None = None,
    on_status: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Explicitly upload one exact clip through YouTube.upload_video()."""
    report = on_status or (lambda message: None)
    reconcile_uploads()
    token = hashlib.sha256(str(clip_id).encode("utf-8")).hexdigest()[:24]
    lock_path = str(Path(clips_config.clips_dir()) / f".youtube-upload-{token}.lock")
    global_lock = _global_lock_path()
    try:
        with ExitStack() as locks:
            # The shared Selenium flow releases Firefox processes before
            # launching its copied profile. Two clip uploads must not do
            # that simultaneously and interrupt each other's transfer.
            locks.enter_context(store.file_lock(global_lock, timeout=0))
            locks.enter_context(store.file_lock(lock_path, timeout=0))
            prepared = validate_upload(clip_id, channel_id, title, description)
            channel = prepared["channel"]
            state = {
                "status": "uploading",
                "channel_id": str(channel["id"]),
                "channel_nickname": str(channel.get("nickname") or channel["id"]),
                "url": "",
                "error": "",
                "title": prepared["title"],
                "description": prepared["description"],
                "started_at": store._now(),
                "finished_at": "",
            }
            if store.update(clip_id, {"youtube_upload": dict(state)}) is None:
                raise UploadError("El clip ya no existe en el catálogo.")
            report(f"Subiendo clip a YouTube: {state['channel_nickname']} (perfil de Firefox configurado)")
            try:
                youtube = _youtube_class()(
                    str(channel["id"]),
                    str(channel.get("nickname") or channel["id"]),
                    prepared["profile"],
                    str(channel.get("niche") or ""),
                    str(channel.get("language") or "español"),
                )
                youtube.video_path = prepared["path"]
                youtube.subject = prepared["title"]
                youtube.metadata = {
                    "title": prepared["title"],
                    "description": prepared["description"],
                }
                youtube._is_long_video = False
                # The normal uploader cleans generated assets after success.
                # Clips stay in their catalogue for preview and downloads.
                youtube._defer_upload_cleanup = True
                succeeded = bool(youtube.upload_video())
                url = _video_url(getattr(youtube, "uploaded_video_url", ""))
                confirmed = bool(getattr(youtube, "upload_confirmed", False)) and bool(url)
                if not succeeded:
                    raise UploadError("La subida no se completó. Revisa el registro y YouTube Studio antes de reintentar.")
            except Exception as exc:
                state.update(status="failed", error=str(exc), finished_at=store._now())
                store.update(clip_id, {"youtube_upload": dict(state)})
                if isinstance(exc, UploadError):
                    raise
                raise UploadError(f"No se pudo subir el clip a YouTube: {exc}") from exc
            state.update(
                status="uploaded" if confirmed else "pending_confirmation",
                url=url,
                finished_at=store._now(),
            )
            changes: dict[str, Any] = {"youtube_upload": dict(state)}
            if confirmed:
                changes.update(status=store.STATUS_PUBLISHED, status_changed_at=store._now())
                report(f"Clip subido a YouTube: {url}")
            else:
                report("La subida todavía no está confirmada como terminada. Revisa el video en YouTube Studio.")
            updated = store.update(clip_id, changes)
            if updated is None:
                raise UploadError("La subida terminó, pero el clip ya no existe en el catálogo.")
            return updated
    except BlockingIOError as exc:
        raise UploadError("Ya hay una subida de clips en curso. Espera a que termine antes de iniciar otra.") from exc
