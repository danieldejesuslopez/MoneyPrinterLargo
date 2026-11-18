"""Verified fact -> original graphics -> MoneyPrinter voice -> vertical batch."""

from __future__ import annotations

import hashlib
import math
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from . import channels, editorial as ed, graphics
from .service import Workspace, event, find, identifier


def _signature(fact: dict) -> str:
    return hashlib.sha256((fact["text"] + "\n" + fact["url"]).encode("utf-8")).hexdigest()


def publish_rendered(workspace: Workspace, request_id: str) -> dict:
    """Explicitly queue upload of finished draft videos, preserving their exact files."""
    with workspace.store.edit() as state:
        job = find(state.get("news_jobs", []), request_id)
        if job["status"] != "done" or job["auto_upload"] or any(i["status"] != "rendered" for i in job["items"]):
            raise ValueError("Solo se puede enviar un lote de borradores terminado y todavía no enviado")
        channels.for_generation(job["channel_id"], True)
        for item in job["items"]:
            fact = find(state["facts"], item["fact_id"])
            if not ed.fresh_fact(fact) or _signature(fact) != item["signature"]:
                raise ValueError("Revisa las fuentes antes de subir: un hecho cambió o perdió vigencia")
        job.update(status="queued", auto_upload=True, error="")
        event(state, "news_batch_upload_requested", request_id)
        return dict(job)


def request(workspace: Workspace, data: dict) -> dict:
    count = int(data.get("count", 2))
    text_count = int(data.get("text_posts", 3))
    if not 1 <= count <= 5 or not 0 <= text_count <= 6:
        raise ValueError("Elige de 1 a 5 videos y de 0 a 6 publicaciones de texto")
    with workspace.store.edit() as state:
        settings = state["settings"]
        channel_id = str(data.get("channel_id") or settings.get("youtube_channel_id", ""))
        auto_upload = bool(data.get("auto_upload", False))
        channel = channels.for_generation(channel_id, auto_upload)
        selected = data.get("fact_ids", [])
        for fact_id in selected:
            find(state["facts"], fact_id)
        candidates = [f for f in state["facts"] if ed.fresh_fact(f) and (not selected or f["id"] in selected)]
        if len(candidates) < count:
            raise ValueError(f"Necesitas {count} hechos oficiales vigentes distintos. Revisa los hechos con más de 48 horas")
        used = {item["signature"] for job in state.get("news_jobs", []) if job["status"] != "cancelled"
                for item in job["items"]}
        if not data.get("allow_reuse", False):
            candidates = [f for f in candidates if _signature(f) not in used]
        if len(candidates) < count:
            raise ValueError("Los hechos disponibles ya están en otro lote. Añade novedades o activa regenerar hechos usados")
        candidates = candidates[:count]
        if any(len(f["text"]) > 900 for f in candidates):
            raise ValueError("Resume cada hecho a 900 caracteres o menos antes de narrarlo")
        row = {"id": identifier(), "status": "queued", "created_at": ed.stamp(), "channel_id": channel_id,
               "channel_name": channel["nickname"], "auto_upload": auto_upload, "text_posts": text_count,
               "text_post_ids": [], "settings": {"name": channel["nickname"], "handle": channel.get("youtube_handle", ""),
                   "language": settings["language"], "voice": settings["voice"] or channel.get("short_voice", "")},
               "items": [{"key": identifier(), "fact_id": f["id"], "fact": dict(f), "signature": _signature(f),
                          "status": "queued", "clip_id": "", "post_id": "", "error": ""} for f in candidates], "error": ""}
        state.setdefault("news_jobs", []).append(row)
        event(state, "news_batch_requested", row["id"])
        return row


def story(fact: dict, settings: dict) -> dict:
    """Keep factual sentences verbatim. A text model cannot add unverified claims."""
    english = settings["language"].lower().startswith(("en", "english", "ingl"))
    title = fact["text"].strip()
    if len(title) > 96:
        title = title[:93].rsplit(" ", 1)[0] + "…"
    intro = "Here is one confirmed detail about GTA six." if english else "Este es un dato confirmado sobre GTA seis."
    closing = "The official source is linked in the description. What do you think?" if english else "La fuente oficial está en la descripción. ¿Qué opinas de este dato?"
    script = f"{intro} {fact['text'].strip()} {closing}"
    description = f"{fact['text']}\n\nFuente oficial: {fact['url']}\n\n{settings['name']} · Fan page independiente.\nTarjetas visuales originales y narración; no es gameplay."
    return {"title": title, "script": script, "description": description, "fact_id": fact["id"]}


def render_story(workspace: Workspace, job: dict, item: dict, on_status=print) -> dict:
    from classes.ScriptVoicePreview import ScriptVoicePreview, ScriptVoicePreviewConfig
    from clips import config as cc, render, segments, store as clip_store

    work = workspace.store.directory / "news" / job["id"] / item["key"]
    work.mkdir(parents=True, exist_ok=True)
    info = story(item["fact"], job["settings"])
    paths = graphics.cards(work, name=job["settings"]["name"], handle=job["settings"]["handle"],
                           title=info["title"], fact=item["fact"]["text"], source=urlsplit(item["fact"]["url"]).hostname or "")
    on_status("GTA 6: creando narración con el TTS de MoneyPrinter")
    voice = ScriptVoicePreview(config=ScriptVoicePreviewConfig(language=job["settings"]["language"], short_voice=job["settings"]["voice"]), output_dir=work)
    preview = voice.synthesize(subject=info["title"], script=info["script"], preview_id=item["key"])
    duration = max(7.0, float(preview.duration_seconds))
    if not math.isfinite(duration) or duration > 180:
        raise ValueError("La narración debe durar hasta 180 segundos; resume el hecho")
    words = segments.parse_words(preview.word_timestamps or [])
    if not words:
        from clips.transcribe import transcribe
        words = segments.parse_words(transcribe(preview.audio_path, str(work), language="en" if job["settings"]["language"].lower().startswith("en") else "es")["words"])
    if not words:
        raise ValueError("No se obtuvieron tiempos de voz para los subtítulos")
    render.write_ass(words, str(work / "narration.ass"), 1080, 1920, cc.font_path(), font_size=76, margin_v=390)
    durations = [duration * 0.20, duration * 0.60, duration * 0.20]
    concat = "".join(f"file '{path.name}'\nduration {seconds:.6f}\n" for path, seconds in zip(paths, durations))
    (work / "cards.ffconcat").write_text(concat + f"file '{paths[-1].name}'\n", encoding="utf-8")
    (work / "script.txt").write_text(info["script"], encoding="utf-8")
    clip_id = item.get("clip_id") or clip_store.new_clip_id()
    output = Path(cc.clips_dir()) / f"{clip_id}.mp4"
    graph = render._filter_graph(1080, 1920, "crop", "narration.ass", cc.fonts_dir())
    failures = []
    for encoder, args in render.encoder_chain():
        on_status(f"GTA 6: render vertical con {encoder} ({duration:.1f} s)")
        command = [render._ffmpeg(), "-y", "-hide_banner", "-nostdin", "-loglevel", "error", "-f", "concat", "-safe", "1",
                   "-i", "cards.ffconcat", "-i", str(Path(preview.audio_path).resolve()), "-filter_complex", graph,
                   "-map", "[v]", "-map", "1:a:0", *args, "-r", "30", "-c:a", "aac", "-b:a", "128k", "-ac", "2",
                   "-af", "loudnorm=I=-14:TP=-1.5:LRA=11,apad", "-t", f"{duration:.3f}", "-pix_fmt", "yuv420p",
                   "-movflags", "+faststart", str(output.resolve())]
        result = subprocess.run(command, cwd=str(work), capture_output=True, text=True, timeout=1200)
        if result.returncode == 0:
            break
        failures.append(f"{encoder}: {result.stderr[-250:]}")
    else:
        raise ValueError("No se pudo renderizar la noticia: " + " | ".join(failures))
    probe = render.probe(str(output))
    if probe.get("width") != 1080 or probe.get("height") != 1920 or not probe.get("has_audio"):
        raise ValueError("El video no superó la comprobación de resolución y audio")
    record = {"id": clip_id, "title": info["title"], "path": str(output), "duration": probe.get("duration", duration),
              "start": 0, "end": duration, "source_id": "gta6-original-" + job["id"], "source_path": "",
              "source_title": job["settings"]["name"] + " · tarjetas originales", "source_url": item["fact"]["url"],
              "source_uploader": job["settings"]["handle"], "rights_note": "Tarjetas y guion propios basados en un hecho oficial con enlace",
              "transcript": info["script"], "words_from": "narrator", "layout": "original_cards", "probe": probe,
              "workspace": "gta6", "gta6_news_key": item["key"], "narration_audio": preview.audio_path,
              "visual_kind": "original_graphics", "fact_ids": [item["fact_id"]]}
    clip_store.add(record)
    return record


def _source(workspace: Workspace, settings: dict) -> dict:
    name = settings["name"] + " · tarjetas originales"
    existing = next((s for s in workspace.store.read()["sources"] if s["name"] == name and s.get("credit") == settings["handle"]), None)
    if existing:
        return existing
    return workspace.source({"name": name, "url": "https://www.youtube.com/" + settings["handle"], "kind": "creator",
                             "monitor_enabled": False,
                             "credit": settings["handle"], "permission": "own", "permission_note": "Diseños originales y narración creados en MoneyPrinter"})


def _update_item(workspace: Workspace, request_id: str, key: str, **changes):
    with workspace.store.edit() as state:
        job = find(state["news_jobs"], request_id)
        find_by_key = next(i for i in job["items"] if i["key"] == key)
        find_by_key.update(changes)


def _upload(workspace: Workspace, job: dict, item: dict, post: dict, on_status):
    from clips import store as clip_store, upload

    channel = channels.for_generation(job["channel_id"], True)
    state = workspace.store.read()
    fact = find(state["facts"], item["fact_id"])
    if not ed.fresh_fact(fact) or _signature(fact) != item["signature"]:
        raise ValueError("El hecho cambió o venció antes de la subida; revisa el borrador")
    # The user explicitly chose upload-after-render for this batch. Keep that approval visible.
    with workspace.store.edit() as state:
        row = find(state["posts"], post["id"])
        row["automation_approval"] = "verified-facts-original-cards"
    workspace.approve(post["id"])
    clip = next(c for c in clip_store.listing() if c["id"] == item["clip_id"])
    previous = clip.get("youtube_upload") or {}
    if previous.get("status") == "uploaded" and previous.get("url"):
        workspace.published(post["id"], previous["url"])
        return "uploaded"
    if previous.get("status") in ("uploading", "pending_confirmation", "failed"):
        return "pending_confirmation"
    updated = upload.upload_clip(item["clip_id"], channel["id"], title=post["title"][:100], description=post["text"], on_status=on_status)
    outcome = updated.get("youtube_upload") or {}
    if outcome.get("status") == "uploaded" and outcome.get("url"):
        workspace.published(post["id"], outcome["url"])
        return "uploaded"
    return "pending_confirmation"


def run(workspace: Workspace, request_id: str, on_status=print) -> dict:
    from clips import store as clip_store

    lock = workspace.store.directory / f"news-{request_id}.lock"
    with clip_store.file_lock(str(lock), timeout=0):
        with workspace.store.edit() as state:
            job = find(state.get("news_jobs", []), request_id)
            if job["status"] == "done":
                return dict(job)
            job.update(status="running", error="", started_at=ed.stamp())
            job = dict(job)
        try:
            channels.for_generation(job["channel_id"], job["auto_upload"])
            source = _source(workspace, job["settings"])
            for item in job["items"]:
                if item["status"] == "uploaded":
                    continue
                existing = next((c for c in clip_store.listing() if c["id"] == item.get("clip_id")), {})
                outcome = existing.get("youtube_upload") or {}
                if outcome.get("status") == "uploaded" and outcome.get("url") and item.get("post_id"):
                    post = find(workspace.store.read()["posts"], item["post_id"])
                    if post["status"] != "published":
                        workspace.published(post["id"], outcome["url"])
                    _update_item(workspace, request_id, item["key"], status="uploaded", error="")
                    continue
                if item["status"] == "pending_confirmation":
                    clip = next((c for c in clip_store.listing() if c["id"] == item["clip_id"]), {})
                    outcome = clip.get("youtube_upload") or {}
                    if outcome.get("status") == "uploaded" and outcome.get("url"):
                        post = find(workspace.store.read()["posts"], item["post_id"])
                        if post["status"] != "published":
                            workspace.published(post["id"], outcome["url"])
                        _update_item(workspace, request_id, item["key"], status="uploaded", error="")
                        continue
                    raise ValueError("Resuelve la subida pendiente en el catálogo de Clips antes de continuar el lote")
                current_fact = find(workspace.store.read()["facts"], item["fact_id"])
                if not ed.fresh_fact(current_fact) or _signature(current_fact) != item["signature"]:
                    raise ValueError("Un hecho del lote cambió o perdió vigencia; revisa sus fuentes antes de continuar")
                clip = next((c for c in clip_store.listing() if c.get("gta6_news_key") == item["key"] and Path(c.get("path", "")).is_file()), None)
                if clip is None:
                    _update_item(workspace, request_id, item["key"], status="rendering", error="")
                    clip = render_story(workspace, job, item, on_status)
                item["clip_id"] = clip["id"]
                info = story(item["fact"], job["settings"])
                post = next((p for p in workspace.store.read()["posts"] if p.get("clip_id") == clip["id"] and p["platform"] == "youtube"), None)
                if post is None:
                    post = workspace.post({"title": info["title"], "text": info["description"], "platform": "youtube", "pillar": "news",
                                           "format": "video", "source_id": source["id"], "fact_ids": [item["fact_id"]], "clip_id": clip["id"],
                                           "hook": info["title"], "commentary": info["script"], "length_sec": clip["duration"]})
                    platforms = [p for p in workspace.store.read()["settings"]["platforms"] if p in ("instagram", "tiktok")]
                    workspace.variants(post["id"], platforms)
                _update_item(workspace, request_id, item["key"], clip_id=clip["id"], post_id=post["id"], status="rendered")
                if job["auto_upload"]:
                    try:
                        status = _upload(workspace, job, item, post, on_status)
                    except Exception as exc:
                        latest = next((c for c in clip_store.listing() if c["id"] == item["clip_id"]), {})
                        upload_state = (latest.get("youtube_upload") or {}).get("status")
                        phase = "pending_confirmation" if upload_state in ("uploading", "pending_confirmation", "failed") else "rendered"
                        _update_item(workspace, request_id, item["key"], status=phase, error=str(exc)[:500])
                        raise
                    _update_item(workspace, request_id, item["key"], status=status)
                    if status == "pending_confirmation":
                        raise ValueError("Una subida requiere comprobar YouTube Studio. No se iniciaron más subidas")
                on_status(f"GTA 6: noticia preparada · {post['title']}")
            with workspace.store.edit() as state:
                saved = find(state["news_jobs"], request_id)
                # Text drafts and their IDs are committed together, making resume idempotent.
                if not saved["text_post_ids"]:
                    for index in range(job["text_posts"]):
                        item = job["items"][index % len(job["items"])]
                        body = item["fact"]["text"] + f"\n\nFuente: {item['fact']['url']}"
                        if index >= len(job["items"]):
                            body = "¿Qué explorarás primero en GTA 6? Cuéntanos tu plan para el primer día."
                        normalized = ed.copy_for(body, "x", job["settings"]["handle"])
                        if any(p["platform"] == "x" and p["text"] == normalized for p in state["posts"]):
                            continue
                        post_id = identifier()
                        state["posts"].append({"id": post_id, "title": item["fact"]["text"][:90] if index < len(job["items"]) else "Tu primer día en GTA 6",
                            "text": normalized, "platform": "x", "pillar": "news" if index < len(job["items"]) else "questions", "format": "text",
                            "source_id": source["id"], "fact_ids": [item["fact_id"]] if index < len(job["items"]) else [], "clip_id": "", "hook": "",
                            "commentary": "", "spoiler": False, "synthetic": False, "reviewed": False, "media_url": "", "idea_id": "", "length_sec": 0,
                            "test_id": "", "created_at": ed.stamp(), "status": "draft", "approved_at": "", "scheduled_at": ""})
                        saved["text_post_ids"].append(post_id)
                saved.update(status="done", finished_at=ed.stamp(), error="")
                return dict(saved)
        except BaseException as exc:
            with workspace.store.edit() as state:
                find(state["news_jobs"], request_id).update(status="error", error=str(exc)[:600], finished_at=ed.stamp())
            raise
