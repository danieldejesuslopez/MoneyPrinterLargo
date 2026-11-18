"""Reconcile only posts produced by the live desk against platform receipts."""
import asyncio
from datetime import timedelta
from importlib.metadata import entry_points

from . import buffer, channels, editorial as ed
from .service import find, identifier
from .autopilot import _avoided, _source_identity


def assign_youtube_channel(state, post):
    """Reserve one destination per clip; retries retain the original account."""
    from .hermes_live import route_for
    if state["settings"].get("hermes_routes") and post.get("status") != "published":
        source = find(state["sources"], post["source_id"])
        route = route_for(state, source["url"])
        if not route:
            raise ValueError("Creador fuera de la lista de Hermes")
        post["hermes_youtube_channel_id"] = route["youtube_channel_id"]
        return route["youtube_channel_id"]
    if post.get("hermes_youtube_channel_id"):
        return post["hermes_youtube_channel_id"]
    destinations = state["settings"].get("hermes_youtube_channel_ids") or [state["settings"]["youtube_channel_id"]]
    destinations = list(dict.fromkeys(c for c in destinations if c))
    if not destinations:
        raise ValueError("Elige las cuentas de YouTube para el reparto de Hermes")
    totals = {c: sum(p.get("platform") == "youtube" and p.get("hermes_youtube_channel_id") == c
                     for p in state["posts"]) for c in destinations}
    post["hermes_youtube_channel_id"] = min(destinations, key=totals.get)
    return post["hermes_youtube_channel_id"]


def prepare(workspace, job_id):
    with workspace.store.edit() as state:
        job = find(state["clip_jobs"], job_id)
        if job["status"] != "done" or not job.get("post_ids"):
            raise ValueError(f"Render {job_id}: {job['status']}")
        for post_id in job["post_ids"]:
            base = find(state["posts"], post_id)
            if base.get("hermes_delivery"):
                continue
            if base["platform"] == "youtube" and (state["settings"].get("hermes_youtube_channel_ids") or state["settings"]["youtube_channel_id"]):
                assign_youtube_channel(state, base)
            source = find(state["sources"], base["source_id"])
            base["hermes_delivery"] = job_id
            base["hermes_original_version"] = base.get("updated_at")
            base["hermes_source_identity"] = _source_identity(source)
            for platform in state["settings"].get("hermes_platforms", ["tiktok", "instagram", "x"]):
                if platform == base["platform"]:
                    continue
                state["posts"].append({**base, "id": identifier(), "platform": platform,
                    "text": ed.copy_for(base["text"], platform, source["credit"], base["spoiler"])})


def deliver(workspace, post_id):
    from clips import store, upload
    state = workspace.store.read()
    post = find(state["posts"], post_id)
    network = post["platform"]
    source = find(state["sources"], post["source_id"])
    from .hermes_live import permitted, route_for
    if not permitted(state, source["url"]):
        raise ValueError("Creador fuera de la lista de Hermes; entrega cancelada")
    route = route_for(state, source["url"])
    if route and (post.get("subtitle_language") != route["language"] or post.get("clip_layout") == "blur"
                  or post.get("hermes_youtube_channel_id") != route["youtube_channel_id"]):
        raise ValueError("El clip requiere el idioma, encuadre y cuenta de la nueva configuración")
    if network not in state["settings"].get("hermes_platforms", ["youtube", "tiktok", "instagram", "x"]):
        raise ValueError("Red fuera de las entregas configuradas para Hermes")
    if (not source["active"] or not source.get("monitor_enabled", True) or _avoided(source, state)
            or source["permission"] not in ("own", "granted") or not source.get("permission_note")
            or _source_identity(source) != post.get("hermes_source_identity")):
        raise ValueError("La fuente o su permiso cambió; revisa esta entrega")
    adapters = entry_points(group="moneyprinter.uploaders", name=network)
    adapter = next(iter(adapters), None)
    if adapter and post.get("hermes_attempt"):
        reconcile_adapter(workspace, post, adapter.load())
        return
    if post["status"] == "scheduled" and post.get("buffer_post_id"):
        receipt = buffer.BufferClient().query("query Receipt($input: PostInput!) { post(input:$input) { id status externalLink } }",
                                              {"input": {"id": post["buffer_post_id"]}}).get("post") or {}
        if receipt.get("status") == "sent" and receipt.get("externalLink"):
            workspace.published(post_id, receipt["externalLink"])
        elif receipt.get("status") == "error":
            raise ValueError("Buffer informa que la publicación falló")
        return
    if post["status"] not in ("draft", "approved", "planned"):
        return
    if not adapter and network == "youtube":
        if not post.get("hermes_youtube_channel_id"):
            with workspace.store.edit() as current:
                assign_youtube_channel(current, find(current["posts"], post_id))
            state = workspace.store.read()
            post = find(state["posts"], post_id)
        channel = channels.for_generation(post["hermes_youtube_channel_id"], True)
    elif not adapter and not (state["settings"]["buffer_channels"].get(network) and post.get("media_url")):
        raise ValueError("Pendiente de módulo de subida o de canal Buffer y URL pública del MP4")
    if post["status"] == "draft":
        # The agent reviewed the capture. Preserve the review requirement after a manual edit.
        if post.get("updated_at") != post.get("hermes_original_version") and not post.get("reviewed"):
            raise ValueError("El borrador editado requiere revisión")
        with workspace.store.edit() as current:
            fresh = find(current["posts"], post_id)
            if fresh != post:
                return
            fresh["reviewed"] = True
        post = workspace.approve(post_id)
    problems = ed.lint_post(post, workspace.store.read())
    if problems:
        raise ValueError(" · ".join(problems))
    if adapter:
        clip = next(c for c in store.listing() if c["id"] == post["clip_id"])
        uploader = adapter.load()
        # Commit intent before the remote side effect. A restarted worker only polls.
        with workspace.store.edit() as current:
            row = find(current["posts"], post_id)
            if row.get("hermes_attempt") or row["status"] != post["status"]:
                return
            row["hermes_attempt"] = post_id
        uploader.submit(key=post_id, path=clip["path"], post=dict(post))
        reconcile_adapter(workspace, post, uploader)
    elif network == "youtube":
        clip = next(c for c in store.listing() if c["id"] == post["clip_id"])
        receipt = clip.get("youtube_upload") or {}
        if receipt.get("status") in ("failed", "uploading", "pending_confirmation"):
            raise ValueError("Revisar el intento existente en YouTube Studio")
        if receipt.get("status") != "uploaded":
            clip = upload.upload_clip(clip["id"], channel["id"], post["title"][:100], post["text"])
            receipt = clip.get("youtube_upload") or {}
        if receipt.get("status") == "uploaded" and receipt.get("url") and receipt.get("channel_id") == channel["id"]:
            workspace.published(post_id, receipt["url"])
        else:
            raise ValueError("Falta confirmación de YouTube")
    else:
        if post["status"] != "planned":
            workspace.plan(post_id, (ed.now() + timedelta(minutes=5)).isoformat())
        buffer.dispatch(workspace, post_id)


def reconcile_adapter(workspace, post, uploader):
    receipt = uploader.receipt(key=post["id"])
    if receipt and receipt.get("status") == "published" and receipt.get("url"):
        workspace.published(post["id"], receipt["url"])
    elif receipt and receipt.get("status") == "failed":
        raise ValueError(str(receipt.get("error") or "Subida fallida"))


async def publish_loop(desk):
    while True:
        posts = desk.workspace.store.read()["posts"]
        for post in posts:
            if not post.get("hermes_delivery") or post["status"] in ("published", "removed", "blocked"):
                continue
            try:
                await asyncio.to_thread(deliver, desk.workspace, post["id"])
                note = ""
            except Exception as exc:
                note = str(exc)[:300]
            if note != post.get("hermes_note", ""):
                with desk.workspace.store.edit() as current:
                    find(current["posts"], post["id"])["hermes_note"] = note
                if note:
                    desk.log(f"{post['platform']} · {post['title']}: {note}")
        await asyncio.sleep(60)
