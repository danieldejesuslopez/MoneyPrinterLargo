"""Bounded watchlist scans and restart-safe production of GTA draft clips."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections import Counter
from datetime import timedelta
from urllib.parse import urlsplit

from . import editorial as ed
from .service import find, identifier


def initial_state() -> dict:
    return {
        "config": {
            "enabled": False, "interval_minutes": 60, "sources_per_scan": 12,
            "videos_per_source": 3, "lookback_hours": 168,
            "max_productions_per_day": 2, "clips_per_video": 1,
            "layout": "blur", "commentary": "¿Qué harías tú en este momento de GTA?",
            "min_seconds": 7, "max_seconds": 30, "use_llm": True,
        },
        "candidates": [], "scans": [], "production_attempts": [],
        "last_scan_at": "", "next_scan_at": "", "source_cursor": 0,
        "last_error": "", "dispatch_until": "", "dispatch_job_id": "",
    }


def _ensure(state: dict) -> dict:
    defaults = initial_state()
    pilot = state.setdefault("autopilot", {})
    for key, value in defaults.items():
        pilot.setdefault(key, copy.deepcopy(value))
    for key, value in defaults["config"].items():
        pilot["config"].setdefault(key, value)
    return pilot


def _day(state: dict) -> str:
    return ed.now().astimezone(ed.get_zone(state["settings"]["timezone"])).date().isoformat()


def _avoided(source: dict, state: dict) -> bool:
    avoided = [s.strip().casefold() for s in re.split(r"[,;\n]+", state["settings"].get("avoid_creators", "")) if s.strip()]
    identity = " ".join(str(source.get(k, "")) for k in ("name", "url", "credit")).casefold()
    return any(name in identity for name in avoided)


def _source_identity(source: dict) -> str:
    """Bind discovery and permission to the same configured channel identity."""
    urls = [str(source.get(key) or "").strip().rstrip("/") for key in ("url", "monitor_url")]
    return hashlib.sha256(json.dumps(urls, ensure_ascii=False).encode("utf-8")).hexdigest()


def _same_identity(candidate: dict, source: dict | None) -> bool:
    return bool(source and candidate.get("source_identity") == _source_identity(source))


def _eligible_source(source: dict, state: dict) -> bool:
    return bool(source.get("active") and source.get("monitor_enabled", True) and source.get("kind") == "creator"
                and source.get("permission") != "denied" and not _avoided(source, state))


def _permission(source: dict | None, state: dict) -> bool:
    return bool(source and _eligible_source(source, state)
                and source.get("permission") in ("own", "granted")
                and str(source.get("permission_note", "")).strip()
                and str(source.get("credit", "")).strip())


def _recent(video: dict, config: dict) -> bool:
    try:
        age = ed.now() - ed.parse_time(video.get("published_at", ""))
    except ValueError:
        return False
    return timedelta(0) <= age <= timedelta(hours=config["lookback_hours"])


def _refresh(state: dict):
    """Reconcile completed requests; never replay an interrupted render."""
    pilot = _ensure(state)
    sources = {s["id"]: s for s in state["sources"]}
    jobs = {j["id"]: j for j in state.get("clip_jobs", [])}
    for candidate in pilot["candidates"]:
        job = jobs.get(candidate.get("request_id"))
        if job and job.get("status") == "done" and candidate["status"] != "ignored":
            if job.get("clip_ids"):
                candidate.update(status="done", clip_ids=list(job.get("clip_ids", [])),
                                 post_ids=list(job.get("post_ids", [])), error="")
            else:
                candidate.update(status="error", error="El productor terminó sin clips; revisa el resultado antes de reintentar.")
        elif candidate["status"] not in ("done", "ignored") and not _same_identity(candidate, sources.get(candidate["source_id"])):
            candidate.update(status="ignored", error="La identidad del canal cambió desde la detección; el candidato anterior no se reutilizará.")
        elif candidate["status"] in ("queued", "producing"):
            candidate.update(status="error", error="Producción interrumpida. Revisa el catálogo y solicita reintentar explícitamente.")
        elif candidate["status"] in ("ready", "needs_permission"):
            if not _recent(candidate, pilot["config"]):
                candidate.update(status="ignored", error="El video salió de la ventana de antigüedad configurada.")
            elif _permission(sources.get(candidate["source_id"]), state):
                candidate.update(status="ready", error="")
            else:
                candidate.update(status="needs_permission", error="Revisa la fuente activa, el permiso, su evidencia y el crédito.")


def snapshot(workspace) -> dict:
    state = workspace.store.read()
    pilot = _ensure(state)
    # Viewing state does not change processing state or interrupted requests.
    for row in pilot["candidates"]:
        if row["status"] in ("ready", "needs_permission"):
            source = next((s for s in state["sources"] if s["id"] == row["source_id"]), None)
            if not _same_identity(row, source):
                row.update(status="ignored", error="La identidad del canal cambió desde la detección; el candidato anterior no se reutilizará.")
            else:
                row["status"] = "ready" if _permission(source, state) else "needs_permission"
    pilot["stats"] = {
        "eligible_sources": sum(_eligible_source(s, state) for s in state["sources"]),
        "pending_permission": sum(c["status"] == "needs_permission" for c in pilot["candidates"]),
        "produced_today": sum(a["day"] == _day(state) for a in pilot["production_attempts"]),
        "counts": dict(Counter(c["status"] for c in pilot["candidates"])),
    }
    try:
        with _lock(workspace):
            pilot["running"] = False
    except BlockingIOError:
        pilot["running"] = True
    return pilot


def configure(workspace, changes: dict) -> dict:
    with workspace.store.edit() as state:
        pilot = _ensure(state)
        if set(changes) - set(initial_state()["config"]):
            raise ValueError("Hay opciones desconocidas del productor automático")
        merged = {**pilot["config"], **changes}
        for key, low, high in (
            ("interval_minutes", 15, 1440), ("sources_per_scan", 1, 100),
            ("videos_per_source", 1, 20), ("lookback_hours", 1, 720),
            ("max_productions_per_day", 0, 20), ("clips_per_video", 1, 3),
        ):
            value = merged[key]
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise ValueError(f"{key} debe estar entre {low} y {high}")
        for key in ("enabled", "use_llm"):
            if not isinstance(merged[key], bool):
                raise ValueError(f"{key} debe ser verdadero o falso")
        for key in ("min_seconds", "max_seconds"):
            value = merged[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("Duración inválida")
        if not 5 <= merged["min_seconds"] < merged["max_seconds"] <= 180:
            raise ValueError("La duración debe cumplir 5 ≤ mínimo < máximo ≤ 180")
        if merged["layout"] not in ("blur", "crop", "facecam"):
            raise ValueError("Encuadre inválido")
        if not isinstance(merged["commentary"], str) or not 1 <= len(merged["commentary"].strip()) <= 2000:
            raise ValueError("Añade un comentario propio de hasta 2000 caracteres")
        merged["commentary"] = merged["commentary"].strip()
        enabled_before = pilot["config"]["enabled"]
        pilot["config"] = merged
        if merged["enabled"]:
            if not enabled_before or not pilot["next_scan_at"]:
                pilot["next_scan_at"] = ed.stamp()
            elif "interval_minutes" in changes:
                pilot["next_scan_at"] = (ed.now() + timedelta(minutes=merged["interval_minutes"])).isoformat()
        else:
            pilot["next_scan_at"] = ""
        return copy.deepcopy(merged)


def _lock(workspace):
    from clips.store import file_lock
    return file_lock(str(workspace.store.directory / "autopilot-run.lock"), timeout=0)


def _existing_media(candidate: dict, state: dict) -> bool:
    from clips import store as clips_store
    from .discovery import canonical_video_key
    request_id = candidate.get("request_id")
    job = next((j for j in state.get("clip_jobs", []) if j["id"] == request_id), {})
    if job.get("clip_ids") or job.get("post_ids"):
        return True
    key = candidate["video_key"]
    for clip in clips_store.listing():
        if request_id and clip.get("gta6_request_id") == request_id:
            return True
        url = clip.get("source_url", "")
        if url and canonical_video_key({"url": url}) == key:
            return True
    return False


def retry(workspace, candidate_id: str) -> dict:
    with _lock(workspace):
        with workspace.store.edit() as state:
            pilot = _ensure(state)
            _refresh(state)
            row = find(pilot["candidates"], candidate_id)
            if row["status"] == "done":
                return copy.deepcopy(row)
            if row["status"] != "error":
                raise ValueError("Solo se pueden reintentar producciones con error")
            if not _recent(row, pilot["config"]):
                raise ValueError("El video salió de la ventana de antigüedad configurada")
            if _existing_media(row, state):
                raise ValueError("Ya hay clips o publicaciones de esta fuente. Revísalos en Clips para evitar duplicados.")
            request_id = row.get("request_id")
            if request_id:
                job = find(state.get("clip_jobs", []), request_id)
                job.update(status="queued", error="")
            source = next((s for s in state["sources"] if s["id"] == row["source_id"]), None)
            row.update(status="ready" if _permission(source, state) else "needs_permission", error="")
            return copy.deepcopy(row)


def ignore(workspace, candidate_id: str) -> dict:
    with _lock(workspace):
        with workspace.store.edit() as state:
            row = find(_ensure(state)["candidates"], candidate_id)
            if row["status"] == "done":
                raise ValueError("La producción ya terminó; revisa sus borradores")
            row.update(status="ignored", error="")
            return copy.deepcopy(row)


def _discover(workspace, scan_id: str, on_status):
    from . import discovery
    with workspace.store.edit() as state:
        pilot = _ensure(state)
        config = dict(pilot["config"])
        sources = [dict(s) for s in state["sources"] if _eligible_source(s, state)]
        cursor = pilot["source_cursor"] % len(sources) if sources else 0
        count = min(config["sources_per_scan"], len(sources))
        selected = [sources[(cursor + offset) % len(sources)] for offset in range(count)]
        pilot["source_cursor"] = (cursor + count) % len(sources) if sources else 0
    for source in selected:
        on_status(f"GTA 6: buscando novedades de {source['name']}")
        try:
            response = discovery.discover_source(source, limit=config["videos_per_source"], timeout=45)
        except Exception as exc:
            response = {"videos": [], "status": "error", "error": f"{type(exc).__name__}: {str(exc)[:400]}"}
        videos = response.get("videos", [])[:config["videos_per_source"]]
        with workspace.store.edit() as state:
            pilot = _ensure(state)
            scan = find(pilot["scans"], scan_id)
            current_source = next((s for s in state["sources"] if s["id"] == source["id"]), None)
            changed_identity = not current_source or _source_identity(source) != _source_identity(current_source)
            if changed_identity:
                response = {**response, "status": "error", "error": "La identidad del canal cambió durante la consulta; se descartaron sus resultados anteriores."}
            added = 0
            known = {c["video_key"] for c in pilot["candidates"]}
            for video in videos:
                if changed_identity or not _eligible_source(current_source, state):
                    break
                if not discovery.is_gta_video(video) or not _recent(video, pilot["config"]):
                    continue
                try:
                    url = ed.web_url(video.get("url", ""))
                except ValueError:
                    continue
                if (urlsplit(url).hostname or "").lower().removeprefix("www.") not in (
                    "youtube.com", "youtu.be", "twitch.tv", "kick.com",
                ):
                    continue
                key = discovery.canonical_video_key(video)
                if not key or key in known:
                    continue
                known.add(key)
                pilot["candidates"].append({
                    "id": identifier(), "video_key": key, "source_id": source["id"],
                    "source_identity": _source_identity(current_source),
                    "title": str(video.get("title", ""))[:500], "url": url,
                    "published_at": video["published_at"], "discovered_at": ed.stamp(),
                    "published_at_estimated": bool(video.get("published_at_estimated", False)),
                    "status": "ready" if _permission(current_source, state) else "needs_permission",
                    "request_id": "", "clip_ids": [], "post_ids": [], "error": "", "attempts": 0,
                })
                added += 1
            scan["sources"].append({"source_id": source["id"], "name": source["name"],
                                    "status": response.get("status", "error"), "found": len(videos),
                                    "new": added, "error": str(response.get("error", ""))[:600]})
            scan["discovered"] += added


def _produce(workspace, scan_id: str, on_status):
    from . import clipping
    with workspace.store.edit() as state:
        _refresh(state)
        candidate_ids = [c["id"] for c in state["autopilot"]["candidates"] if c["status"] == "ready"]
    for candidate_id in candidate_ids:
        with workspace.store.edit() as state:
            pilot = _ensure(state)
            config = dict(pilot["config"])
            day = _day(state)
            used = sum(a["day"] == day for a in pilot["production_attempts"])
            if not config["enabled"] or used >= config["max_productions_per_day"]:
                break
            candidate = find(pilot["candidates"], candidate_id)
            source = next((s for s in state["sources"] if s["id"] == candidate["source_id"]), None)
            if candidate["status"] != "ready":
                continue
            if not _same_identity(candidate, source):
                candidate.update(status="ignored", error="La identidad del canal cambió desde la detección; el candidato anterior no se reutilizará.")
                continue
            if not _permission(source, state):
                candidate.update(status="needs_permission", error="Revisa el permiso y el crédito de la fuente.")
                continue
            if not _recent(candidate, config):
                candidate.update(status="ignored", error="El video salió de la ventana de antigüedad configurada.")
                continue
            if _existing_media(candidate, state):
                candidate.update(status="ignored", error="El video ya tiene clips o publicaciones en el catálogo; no se repitió la producción.")
                continue
            candidate.update(status="queued", attempts=candidate.get("attempts", 0) + 1,
                             claimed_at=ed.stamp(), claim_day=day, error="")
            pilot["production_attempts"].append({"id": identifier(), "candidate_id": candidate_id,
                                                 "day": day, "claimed_at": ed.stamp()})
            candidate = dict(candidate)
        try:
            request_id = candidate.get("request_id")
            if not request_id:
                job = clipping.request(workspace, {
                    "autopilot_candidate_id": candidate_id, "source_id": candidate["source_id"],
                    "target": candidate["url"], "mode": "auto", "count": config["clips_per_video"],
                    "min_seconds": config["min_seconds"], "max_seconds": config["max_seconds"],
                    "layout": config["layout"], "commentary": config["commentary"],
                    "use_llm": config["use_llm"], "platform": "youtube", "subtitles": True,
                })
                request_id = job["id"]
            with workspace.store.edit() as state:
                pilot = _ensure(state)
                row = find(pilot["candidates"], candidate_id)
                # Persist before running even when a test/different requester has no atomic binding.
                row["request_id"] = request_id
                source = next((s for s in state["sources"] if s["id"] == row["source_id"]), None)
                if not _same_identity(row, source):
                    row.update(status="ignored", error="La identidad del canal cambió antes del render; el candidato anterior no se reutilizará.")
                    continue
                if not pilot["config"]["enabled"] or not _permission(source, state):
                    row.update(status="error", error="Producción pausada o permiso retirado antes del render.")
                    continue
                row["status"] = "producing"
            on_status(f"GTA 6: produciendo borradores de {candidate['title']}")
            result = clipping.run(workspace, request_id, on_status=on_status)
            with workspace.store.edit() as state:
                row = find(_ensure(state)["candidates"], candidate_id)
                job = next((j for j in state.get("clip_jobs", []) if j["id"] == request_id), {})
                clips = job.get("clip_ids") or [c["id"] for c in result.get("clips", [])]
                posts = job.get("post_ids") or [p["id"] for p in result.get("posts", [])]
                if not clips:
                    raise ValueError("El productor no generó clips; revisa el resultado antes de reintentar")
                row.update(status="done", clip_ids=clips, post_ids=posts, error="")
                find(state["autopilot"]["scans"], scan_id)["produced"] += 1
        except Exception as exc:
            with workspace.store.edit() as state:
                row = find(_ensure(state)["candidates"], candidate_id)
                row.update(status="error", error=f"{type(exc).__name__}: {str(exc)[:500]}")
                find(state["autopilot"]["scans"], scan_id)["failed"] += 1
            on_status(f"GTA 6: producción pendiente de revisar · {candidate['title']}")


def run(workspace, produce: bool = True, on_status=print) -> dict:
    with _lock(workspace):
        with workspace.store.edit() as state:
            pilot = _ensure(state)
            _refresh(state)
            for previous in pilot["scans"]:
                if previous.get("status") == "running":
                    previous.update(status="error", finished_at=ed.stamp(),
                                    error="El ciclo anterior fue interrumpido antes de terminar.")
            scan = {"id": identifier(), "started_at": ed.stamp(), "finished_at": "", "status": "running",
                    "sources": [], "discovered": 0, "produced": 0, "failed": 0, "error": ""}
            pilot["scans"] = pilot["scans"][-19:] + [scan]
            pilot["last_scan_at"] = scan["started_at"]
            pilot["last_error"] = ""
            scan_id = scan["id"]
        try:
            _discover(workspace, scan_id, on_status)
            if produce:
                _produce(workspace, scan_id, on_status)
            with workspace.store.edit() as state:
                find(_ensure(state)["scans"], scan_id)["status"] = "done"
        except BaseException as exc:
            with workspace.store.edit() as state:
                pilot = _ensure(state)
                error = f"{type(exc).__name__}: {str(exc)[:600]}"
                pilot["last_error"] = error
                find(pilot["scans"], scan_id).update(status="error", error=error)
            raise
        finally:
            with workspace.store.edit() as state:
                pilot = _ensure(state)
                find(pilot["scans"], scan_id)["finished_at"] = ed.stamp()
                pilot["next_scan_at"] = ((ed.now() + timedelta(minutes=pilot["config"]["interval_minutes"])).isoformat()
                                         if pilot["config"]["enabled"] else "")
                pilot["dispatch_until"] = ""
    return snapshot(workspace)
