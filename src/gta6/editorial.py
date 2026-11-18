"""Editorial checks, platform copy, and learning from real observations."""

from __future__ import annotations

import math
import re
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .store import PILLARS, PLATFORMS

OFFICIAL_HOSTS = ("rockstargames.com", "take2games.com", "playstation.com", "xbox.com")
FACT_PILLARS = ("news", "tips", "spotlight", "countdown")
METRIC_FIELDS = ("views", "likes", "reposts", "replies", "saves", "bookmarks", "follows", "retention")
PROHIBITED = re.compile(
    r"\b(leaked|leak|filtraci[oó]n|filtrado|crack|keygen|pirateado|play early|jugar antes|"
    r"gratis.*(?:copia|key)|free cop(?:y|ies)|truco.*(?:regi[oó]n|nueva zelanda))\b", re.I,
)


def now() -> datetime:
    return datetime.now(timezone.utc)


def stamp() -> str:
    return now().isoformat(timespec="seconds")


def parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("Fecha inválida; usa ISO 8601 con zona horaria") from exc
    if parsed.tzinfo is None:
        raise ValueError("La fecha necesita zona horaria")
    return parsed.astimezone(timezone.utc)


def get_zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
        raise ValueError("Zona horaria IANA inválida; por ejemplo America/La_Paz") from exc


def local_time(value: str, zone: str) -> datetime:
    """datetime-local controls express the workspace's zone, not the browser's."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError) as exc:
        raise ValueError("Fecha u hora inválida") from exc
    if parsed.tzinfo is None:
        local = parsed.replace(tzinfo=get_zone(zone))
        utc = local.astimezone(timezone.utc)
        if utc.astimezone(get_zone(zone)).replace(tzinfo=None) != parsed:
            raise ValueError("Esta hora no existe por el cambio de horario; elige otra")
        return utc
    return parsed.astimezone(timezone.utc)


def web_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Se necesita una URL http(s) sin credenciales")
    return value.strip()


def official_url(value: str) -> bool:
    host = (urlsplit(value).hostname or "").lower()
    return any(host == domain or host.endswith("." + domain) for domain in OFFICIAL_HOSTS)


def fresh_fact(fact: dict, at: datetime | None = None) -> bool:
    if fact.get("status") != "verified" or not official_url(fact.get("url", "")):
        return False
    try:
        age = (at or now()) - parse_time(fact["verified_at"])
        return timedelta(0) <= age <= timedelta(hours=48)
    except (ValueError, KeyError):
        return False


def copy_for(text: str, platform: str, credit: str = "", spoiler: bool = False) -> str:
    text = text.strip()
    # Never silently truncate a claim, URL or attribution to meet a limit.
    if platform == "x":
        text = re.sub(r"(?<!\w)#[\w]+", "", text).strip()
    if credit and credit.casefold() not in text.casefold():
        text += f"\n\nCrédito: {credit}"
    if spoiler and not re.search(r"\bspoiler\b", text, re.I):
        text = "SPOILER\n" + text
    if platform == "instagram" and not re.search(r"#\w+", text):
        text += "\n\n#GTA6 #GTAVI #ViceCity"
    return text


def lint_post(post: dict, state: dict) -> list[str]:
    errors = []
    platform, pillar = post.get("platform"), post.get("pillar")
    if platform not in PLATFORMS or pillar not in PILLARS:
        errors.append("Elige una plataforma y un pilar válidos")
    if platform in ("youtube", "tiktok") and post.get("format") != "video":
        errors.append("Esta red necesita un video para este flujo")
    if platform == "instagram" and post.get("format") == "text":
        errors.append("Instagram necesita una imagen o un video")
    text = post.get("text", "").strip()
    if not text:
        errors.append("El texto está vacío")
    limits = {"x": 280, "instagram": 2200, "tiktok": 2200, "youtube": 5000, "threads": 500, "bluesky": 300}
    if len(text) > limits.get(platform, 2200):
        errors.append(f"El texto supera {limits[platform]} caracteres en esta red")
    if PROHIBITED.search(text):
        errors.append("Revisar referencias a filtraciones, copias o acceso anticipado")
    if re.search(r"\b(rumor|supuestamente|se dice|unconfirmed)\b", text, re.I):
        errors.append("Los rumores necesitan revisión específica; usa solo información verificada")
    if pillar in FACT_PILLARS and not post.get("fact_ids"):
        errors.append("Vincula al menos un hecho oficial verificado")
    facts = {f["id"]: f for f in state["facts"]}
    for fact_id in post.get("fact_ids", []):
        if fact_id not in facts or not fresh_fact(facts[fact_id]):
            errors.append("Hay hechos sin verificar, retirados o con más de 48 h; vuelve a comprobarlos")
    if post.get("scheduled_at"):
        due = parse_time(post["scheduled_at"])
        if due > now() and any(fact_id not in facts or not fresh_fact(facts[fact_id], due) for fact_id in post.get("fact_ids", [])):
            errors.append("Los hechos vencerán antes de ese horario; programa más cerca de la revisión")
    source = next((s for s in state["sources"] if s["id"] == post.get("source_id")), None)
    if source:
        if not source.get("active") or source.get("permission") == "denied":
            errors.append("La fuente está bloqueada o retiró su permiso")
        if source.get("kind") == "discovery":
            errors.append("Una fuente de descubrimiento no sustituye al creador original")
        if source.get("kind") == "creator" and source.get("permission") not in ("granted", "own"):
            errors.append("Falta permiso del creador")
        if post.get("format") == "video" and source.get("permission") not in ("granted", "own"):
            errors.append("Registra la base de uso del material en el permiso de la fuente")
        credit = source.get("credit", "")
        if not credit or credit.casefold() not in text.casefold():
            errors.append("Falta el crédito de la fuente en el texto")
    elif post.get("format") == "video":
        errors.append("El clip necesita su fuente original")
    if post.get("format") == "video":
        if not post.get("clip_id"):
            errors.append("Vincula un clip renderizado")
        if post.get("synthetic"):
            errors.append("No se permite presentar gameplay generado por IA como real")
        if not post.get("hook") or not post.get("commentary"):
            errors.append("Añade gancho y comentario o valor propio al clip")
        if not 0 < post.get("length_sec", 0) <= 180:
            errors.append("Registra una duración de video mayor que cero y de hasta 180 segundos")
    if post.get("spoiler") and "spoiler" not in text.casefold():
        errors.append("Marca SPOILER en el texto")
    if not post.get("reviewed") and post.get("automation_approval") != "verified-facts-original-cards":
        errors.append("Revisa el texto, los hechos y el video antes de aprobar")
    fingerprint = re.sub(r"\s+", " ", text.casefold()).strip()
    for other in state["posts"]:
        if (other["id"] != post.get("id") and other.get("platform") == platform
                and other.get("status") not in ("removed", "blocked")
                and re.sub(r"\s+", " ", other.get("text", "").casefold()).strip() == fingerprint):
            try:
                if now() - parse_time(other["created_at"]) <= timedelta(days=7):
                    errors.append("Este texto ya existe en esta red en los últimos siete días")
                    break
            except (ValueError, KeyError):
                pass
    return list(dict.fromkeys(errors))


def review(state: dict, days: int = 7) -> dict:
    """Compare same-age reads; never mix a 12 h view count with a 72 h count."""
    at = now()
    posts = []
    for post in state["posts"]:
        if post.get("status") != "published" or not post.get("published_at"):
            continue
        age = (at - parse_time(post["published_at"])).total_seconds() / 3600
        if 0 <= age <= days * 24:
            posts.append((post, age))
    eligible = []
    for horizon in (72, 24):
        eligible = []
        for post, age in posts:
            metric = next((m for m in state["metrics"] if m["post_id"] == post["id"] and m["hours"] == horizon), None)
            if age >= horizon and metric and metric.get("views") is not None:
                eligible.append({**post, "views": metric["views"], "read_hours": horizon})
        if eligible:
            break
    groups = defaultdict(list)
    zone = get_zone(state["settings"]["timezone"])
    for post in eligible:
        slot = parse_time(post["published_at"]).astimezone(zone).strftime("%H:00")
        for dimension, value in (("platform", post["platform"]), ("pillar", post["pillar"]), ("slot", slot)):
            groups[(dimension, value)].append(post["views"])
    ranked = sorted(eligible, key=lambda p: p["views"], reverse=True)
    due = []
    for post, age in posts:
        for hours in (12, 24, 72):
            if age >= hours and not any(m["post_id"] == post["id"] and m["hours"] == hours for m in state["metrics"]):
                due.append({"post_id": post["id"], "title": post["title"], "hours": hours})
    return {
        "days": days, "sample": len(eligible), "read_hours": eligible[0]["read_hours"] if eligible else None,
        "top": ranked[:3], "bottom": list(reversed(ranked[3:][-3:])),
        "medians": [{"dimension": k[0], "value": k[1], "median": statistics.median(v), "sample": len(v)} for k, v in groups.items()],
        "due": due, "note": "Se comparan lecturas de la misma edad. Las 12 h solo sirven como señal temprana. Las muestras pequeñas no prueban causalidad.",
    }


def validate_metric(data: dict) -> dict:
    result = {}
    for field in METRIC_FIELDS:
        value = data.get(field)
        if value in (None, ""):
            result[field] = None
            continue
        if isinstance(value, bool):
            raise ValueError(f"{field}: usa un número")
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field}: usa un número") from exc
        if not math.isfinite(number) or number < 0 or (field == "retention" and number > 100):
            raise ValueError(f"{field}: valor fuera de rango")
        if field != "retention" and not number.is_integer():
            raise ValueError(f"{field}: usa un número entero")
        result[field] = number if field == "retention" else int(number)
    return result
