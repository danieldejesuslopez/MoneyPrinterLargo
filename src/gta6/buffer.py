"""Buffer's current GraphQL API. External mutations require an explicit action."""

from __future__ import annotations

import ipaddress
import os
from urllib.parse import urlsplit

import requests

from . import editorial as ed
from .service import Workspace, event, find

ENDPOINT = "https://api.buffer.com"
SERVICE = {"x": "twitter", "instagram": "instagram", "tiktok": "tiktok", "youtube": "youtube", "threads": "threads", "bluesky": "bluesky"}


class BufferError(RuntimeError):
    pass


class BufferClient:
    def __init__(self, token: str = ""):
        self.token = token or os.environ.get("BUFFER_API_KEY", "")
        if not self.token:
            raise BufferError("Configura BUFFER_API_KEY en el entorno del backend y reinícialo")

    def query(self, query: str, variables: dict | None = None) -> dict:
        try:
            response = requests.post(ENDPOINT, headers={"Authorization": f"Bearer {self.token}"},
                                     json={"query": query, "variables": variables or {}}, timeout=(10, 45))
        except requests.RequestException as exc:
            raise BufferError("Buffer no confirmó la respuesta. Revisa su cola antes de reintentar.") from exc
        if not response.ok:
            raise BufferError(f"Buffer respondió HTTP {response.status_code}. Revisa la cola antes de reintentar.")
        try:
            payload = response.json()
        except ValueError as exc:
            raise BufferError("Respuesta ilegible de Buffer; revisa su cola") from exc
        if payload.get("errors"):
            raise BufferError("Buffer rechazó la consulta. Revisa los permisos y la configuración del canal.")
        return payload.get("data") or {}

    def channels(self) -> list[dict]:
        data = self.query("query { account { organizations { id } } }")
        rows = []
        for org in (data.get("account") or {}).get("organizations", []):
            channels = self.query("query Channels($input: ChannelsInput!) { channels(input: $input) { id name service isQueuePaused } }",
                                  {"input": {"organizationId": org["id"]}})
            rows.extend(channels.get("channels", []))
        return rows

    def create(self, post: dict, channel_id: str) -> dict:
        data = {"text": post["text"], "channelId": channel_id, "schedulingType": "automatic",
                "mode": "customScheduled", "dueAt": post["scheduled_at"], "assets": [],
                "aiAssisted": True, "needsApproval": False}
        if post["format"] in ("video", "image"):
            kind = "video" if post["format"] == "video" else "image"
            data["assets"] = [{kind: {"url": post["media_url"]}}]
        if post["platform"] == "instagram":
            data["metadata"] = {"instagram": {"type": "reel" if post["format"] == "video" else "post", "shouldShareToFeed": True}}
        elif post["platform"] == "youtube":
            data["metadata"] = {"youtube": {"title": post["title"][:100], "categoryId": "20", "madeForKids": False}}
        result = self.query("""mutation Schedule($input: CreatePostInput!) {
            createPost(input: $input) {
                ... on PostActionSuccess { post { id text dueAt } }
                ... on MutationError { message }
            }
        }""", {"input": data}).get("createPost") or {}
        if not result.get("post", {}).get("id"):
            raise BufferError("Buffer no confirmó la creación. Revisa la cola y los requisitos de la red.")
        return result["post"]


def public_media_url(value: str):
    url = ed.web_url(value)
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host in ("localhost", "localhost.localdomain") or host.endswith((".local", ".localhost")):
        raise ValueError("Buffer necesita un archivo HTTPS público y permanente, accesible sin iniciar sesión")
    try:
        if not ipaddress.ip_address(host).is_global:
            raise ValueError("Buffer no puede leer una dirección privada")
    except ValueError as exc:
        if "dirección privada" in str(exc):
            raise
    if parsed.query or parsed.fragment:
        raise ValueError("Usa una URL directa y permanente, sin firma temporal ni parámetros")
    if not parsed.path.lower().endswith((".mp4", ".jpg", ".jpeg", ".png", ".webp")):
        raise ValueError("La URL debe apuntar al archivo MP4 o imagen, no a una página para compartir")


def dispatch(workspace: Workspace, post_id: str, client: BufferClient | None = None) -> dict:
    client = client or BufferClient()
    # Validate account mapping before claiming the mutation. Never hold a DB lock over HTTP.
    channels = client.channels()
    with workspace.store.edit() as state:
        post = find(state["posts"], post_id)
        if post["status"] != "planned":
            raise ValueError("Solo se puede enviar una reserva local; revisa antes cualquier intento pendiente")
        if ed.parse_time(post["scheduled_at"]) <= ed.now():
            raise ValueError("El horario ya pasó; reserva uno nuevo")
        issues = ed.lint_post(post, state)
        if issues:
            raise ValueError(" · ".join(issues))
        channel_id = state["settings"]["buffer_channels"].get(post["platform"], "")
        channel = next((c for c in channels if c["id"] == channel_id), None)
        if not channel or channel.get("service") != SERVICE[post["platform"]] or channel.get("isQueuePaused"):
            raise ValueError("El canal Buffer no coincide con la red o su cola está pausada")
        if post["format"] in ("video", "image"):
            public_media_url(post.get("media_url", ""))
        post.update(status="dispatching", buffer_attempt_at=ed.stamp())
        submitted = dict(post)
        event(state, "buffer_attempt", post_id)
    try:
        remote = client.create(submitted, channel_id)
    except Exception:
        with workspace.store.edit() as state:
            row = find(state["posts"], post_id)
            row.update(buffer_note="Resultado no confirmado: revisa Buffer. No se reintenta automáticamente.")
            if row["status"] == "dispatching":
                row["status"] = "pending_confirmation"
        raise
    with workspace.store.edit() as state:
        row = find(state["posts"], post_id)
        row.update(buffer_post_id=remote["id"], buffer_due_at=remote.get("dueAt", ""), buffer_note="")
        if row["status"] == "dispatching":
            row["status"] = "scheduled"
        else:
            row["buffer_note"] = "El post cambió mientras se enviaba. Cancela la programación remota en Buffer."
        event(state, "buffer_scheduled", post_id)
        return row


def resolve(workspace: Workspace, post_id: str, outcome: str, remote_id: str = "") -> dict:
    with workspace.store.edit() as state:
        row = find(state["posts"], post_id)
        if row["status"] not in ("pending_confirmation", "dispatching"):
            raise ValueError("No hay un intento pendiente de resolver")
        if row["status"] == "dispatching" and (ed.now() - ed.parse_time(row["buffer_attempt_at"])).total_seconds() < 120:
            raise ValueError("El envío puede seguir activo; espera dos minutos y revisa Buffer")
        if outcome == "scheduled" and remote_id.strip():
            row.update(status="scheduled", buffer_post_id=remote_id.strip(), buffer_note="Confirmado manualmente en Buffer")
        elif outcome == "not_created":
            row.update(status="approved", scheduled_at="", buffer_note="Se comprobó que no existe en Buffer; se puede reservar otra vez")
        else:
            raise ValueError("Indica scheduled con el ID remoto o not_created después de revisar Buffer")
        event(state, "buffer_resolved", post_id)
        return row
