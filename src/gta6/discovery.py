"""Bounded public metadata discovery for the GTA creator watchlist.

No media, browser profile or cookies are read. A channel listing is evidence
of a possible source, never permission to reuse its videos or verify its claims.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit


_YOUTUBE_ID = re.compile(r"^[\w-]{11}$", re.ASCII)
_KICK_ID = re.compile(r"^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$", re.I)
_GTA = re.compile(r"\b(?:gta(?:[\s:_-]*(?:[56]|vi?|online|rp))?|grand\s+theft\s+auto|nopixel|marbella\s+vice)\b", re.I)
_LEAK = re.compile(r"\b(?:leak(?:ed|s|ing)?|filtrad[oa]s?|filtraci[oó]n(?:es)?)\b", re.I)
_LIVE = {"is_live", "is_upcoming", "post_live"}


def _host(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
        raise ValueError("Se necesita una URL pública http(s) sin credenciales")
    if parsed.port not in (None, 80, 443):
        raise ValueError("El puerto de la fuente no es compatible")
    return (parsed.hostname or "").lower().removeprefix("www.").removeprefix("m.")


def _youtube_id(url: str) -> str:
    try:
        host = _host(url)
        parsed = urlsplit(url)
    except (ValueError, TypeError):
        return ""
    parts = parsed.path.strip("/").split("/")
    if host == "youtu.be":
        value = parts[0]
    elif host == "youtube.com":
        value = parse_qs(parsed.query).get("v", [""])[0]
        if len(parts) == 2 and parts[0] in ("shorts", "live", "embed"):
            value = parts[1]
    else:
        return ""
    return value if _YOUTUBE_ID.fullmatch(value) else ""


def canonical_video_key(video: dict) -> str:
    """Return a provider-scoped ID, stable across watch/short/share URLs."""
    url = str(video.get("url") or video.get("webpage_url") or "")
    youtube_id = _youtube_id(url)
    if youtube_id:
        return "youtube:" + youtube_id
    try:
        host = _host(url)
        parts = urlsplit(url).path.strip("/").split("/")
    except (ValueError, TypeError):
        return ""
    if host == "twitch.tv" and len(parts) == 2 and parts[0] == "videos" and parts[1].isdigit():
        return "twitch:" + parts[1]
    if host == "kick.com" and len(parts) == 3 and parts[1] == "videos" and _KICK_ID.fullmatch(parts[2]):
        return "kick:" + parts[2].lower()
    return ""


def is_gta_video(video: dict) -> bool:
    """Conservative relevance filter, using public text as data only."""
    if video.get("is_live") or video.get("live_status") in _LIVE:
        return False
    title = str(video.get("title") or "")
    metadata = [title, str(video.get("description") or "")]
    for field in ("categories", "tags"):
        values = video.get(field) or []
        metadata.extend(str(value) for value in values if isinstance(value, str))
    text = "\n".join(metadata)
    return bool(_GTA.search(text)) and not bool(_LEAK.search(text))


def _source_url(source: dict) -> tuple[str, str]:
    url = str(source.get("monitor_url") or source.get("url") or "").strip()
    host = _host(url)
    parsed = urlsplit(url)
    parts = parsed.path.strip("/").split("/")
    if host in ("youtube.com", "youtu.be"):
        video_id = _youtube_id(url)
        if video_id:
            return "youtube", "https://www.youtube.com/watch?v=" + video_id
        if host == "youtube.com" and (
            (parts[0].startswith("@") and len(parts[0]) > 1)
            or (parts[0] in ("channel", "c", "user") and len(parts) > 1 and parts[1])
        ):
            end = 1 if parts[0].startswith("@") else 2
            return "youtube", "https://www.youtube.com/" + "/".join(parts[:end]) + "/videos"
        raise ValueError("Usa el canal de YouTube con @handle o /channel/ID")
    if host == "twitch.tv":
        if len(parts) == 2 and parts[0] == "videos" and parts[1].isdigit():
            return "twitch", "https://www.twitch.tv/videos/" + parts[1]
        if parts[0] and re.fullmatch(r"[\w]{2,50}", parts[0], re.ASCII) and parts[0] not in ("directory", "search", "videos", "settings"):
            return "twitch", "https://www.twitch.tv/" + parts[0] + "/videos?filter=archives&sort=time"
        raise ValueError("Usa el perfil público o la URL de una grabación de Twitch")
    if host == "kick.com":
        if len(parts) == 3 and parts[1] == "videos" and _KICK_ID.fullmatch(parts[2]) and re.fullmatch(r"[\w-]+", parts[0], re.ASCII):
            return "kick", "https://kick.com/" + "/".join(parts)
        raise NotImplementedError("La versión instalada de yt-dlp no enumera archivos de canales Kick; configura una URL YouTube/Twitch de seguimiento o un VOD de Kick")
    raise NotImplementedError("La búsqueda automática admite fuentes públicas de YouTube y Twitch, y videos individuales de Kick")


def _published_at(entry: dict) -> str:
    for key in ("release_timestamp", "timestamp"):
        value = entry.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            try:
                return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="seconds")
            except (ValueError, OverflowError, OSError):
                pass
    for key in ("upload_date", "release_date"):
        value = entry.get(key)
        if isinstance(value, str) and re.fullmatch(r"\d{8}", value):
            try:
                return datetime.strptime(value, "%Y%m%d").replace(tzinfo=timezone.utc).isoformat(timespec="seconds")
            except ValueError:
                pass
    return ""


def _normalize(entry: dict, playlist: dict, source: dict, provider: str) -> dict | None:
    video_id = str(entry.get("id") or "")
    candidate_url = str(entry.get("webpage_url") or entry.get("url") or "")
    if provider == "youtube":
        video_id = _youtube_id(candidate_url) or (video_id if _YOUTUBE_ID.fullmatch(video_id) else "")
        url = "https://www.youtube.com/watch?v=" + video_id if video_id else ""
    elif provider == "twitch":
        video_id = video_id.removeprefix("v")
        url = "https://www.twitch.tv/videos/" + video_id if video_id.isdigit() else ""
    else:
        url = candidate_url if canonical_video_key({"url": candidate_url}).startswith("kick:") else str(source.get("monitor_url") or source.get("url") or "")
    key = canonical_video_key({"url": url})
    if not key:
        return None
    video_id = key.split(":", 1)[1]
    duration = entry.get("duration")
    duration = float(duration) if isinstance(duration, (int, float)) and not isinstance(duration, bool) and math.isfinite(duration) and duration > 0 else None
    return {
        "video_id": video_id, "url": url, "title": str(entry.get("title") or ""),
        "published_at": _published_at(entry), "duration": duration,
        "is_live": bool(entry.get("is_live")) or entry.get("live_status") in _LIVE,
        "uploader": str(entry.get("uploader") or entry.get("channel") or playlist.get("uploader") or playlist.get("channel") or source.get("name") or ""),
        "channel_id": str(entry.get("channel_id") or playlist.get("channel_id") or ""),
        "categories": entry.get("categories") if isinstance(entry.get("categories"), list) else [],
        "description": str(entry.get("description") or ""),
        "published_at_estimated": provider == "youtube" and entry.get("_type") in ("url", "url_transparent") and bool(entry.get("timestamp")) and not entry.get("upload_date"),
    }


def _read_metadata(url: str, limit: int, timeout: float, *, flat: bool) -> tuple[dict | None, str, int]:
    command = [sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-cache-dir", "--skip-download",
               "--dump-single-json", "--ignore-errors", "--no-progress", "--socket-timeout", str(min(15, timeout)),
               "--retries", "1", "--extractor-retries", "1"]
    if flat:
        command.extend(["--flat-playlist", "--playlist-end", str(limit), "--extractor-args", "youtubetab:approximate_date=true"])
    else:
        command.extend(["--no-playlist", "--ignore-no-formats-error"])
    command.append(url)
    try:
        process = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, f"La consulta superó {timeout:g} segundos", 1
    except OSError as exc:
        return None, f"No se pudo iniciar yt-dlp: {str(exc)[:400]}", 1
    diagnostic = " ".join((process.stderr or "").split())[-600:]
    try:
        metadata = json.loads(process.stdout or "")
        if not isinstance(metadata, dict):
            raise ValueError("La respuesta no es un objeto")
    except (ValueError, TypeError):
        return None, diagnostic or "yt-dlp no devolvió metadatos válidos", 1
    return metadata, diagnostic if process.returncode or "ERROR:" in diagnostic else "", process.returncode


def _identity_problem(url: str, metadata: dict, provider: str) -> bool:
    """Reject a concrete identity mismatch; missing identity stays unclaimed."""
    parts = urlsplit(url).path.strip("/").split("/")
    if provider == "youtube" and parts[0].startswith("@"):
        actual = str(metadata.get("uploader_id") or "")
        return actual.startswith("@") and actual.casefold() != parts[0].casefold()
    if provider == "youtube" and parts[0] == "channel" and len(parts) > 1:
        actual = str(metadata.get("channel_id") or metadata.get("id") or "")
        return actual.startswith("UC") and actual != parts[1]
    if provider == "twitch" and parts[0] != "videos":
        actual = str(metadata.get("id") or "")
        return bool(actual) and actual.casefold() != parts[0].casefold()
    return False


def discover_source(source: dict, *, limit: int = 5, timeout: int = 45) -> dict:
    """List at most ``limit`` videos, with an enforced subprocess deadline.

    An ``ok`` result may carry an error note when yt-dlp recovered part of a
    playlist. Errors are visible per source and never turn into invented videos.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 30:
        raise ValueError("El límite de videos debe estar entre 1 y 30")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 1 <= timeout <= 180:
        raise ValueError("El tiempo máximo debe estar entre 1 y 180 segundos")
    try:
        provider, url = _source_url(source)
    except NotImplementedError as exc:
        return {"videos": [], "status": "unsupported", "error": str(exc)}
    except (ValueError, TypeError, IndexError) as exc:
        return {"videos": [], "status": "error", "error": str(exc)}
    deadline = time.monotonic() + timeout
    playlist, diagnostic, returncode = _read_metadata(url, limit, timeout, flat=True)
    if playlist is None:
        return {"videos": [], "status": "error", "error": diagnostic}
    if _identity_problem(url, playlist, provider):
        return {"videos": [], "status": "error", "error": "La identidad del canal devuelto no coincide con la URL de seguimiento"}
    entries = playlist.get("entries") if "entries" in playlist else [playlist]
    if not isinstance(entries, list):
        entries = []
    videos, seen = [], set()
    root_channel_id = str(playlist.get("channel_id") or "")
    problems = [diagnostic] if diagnostic else []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if root_channel_id and entry.get("channel_id") and entry["channel_id"] != root_channel_id:
            problems.append("Se descartó una entrada atribuida a otro canal")
            continue
        video = _normalize(entry, playlist, source, provider)
        key = canonical_video_key(video) if video else ""
        if video and key not in seen:
            seen.add(key)
            remaining = deadline - time.monotonic()
            if not video["published_at"] and is_gta_video(video) and remaining >= 1:
                details, error, _ = _read_metadata(video["url"], 1, remaining, flat=False)
                if details:
                    detailed = _normalize(details, playlist, source, provider)
                    mismatched_channel = root_channel_id and details.get("channel_id") and details["channel_id"] != root_channel_id
                    if detailed and canonical_video_key(detailed) == key and not mismatched_channel:
                        video = detailed
                    else:
                        problems.append("La identidad del video detallado no coincide con la entrada del canal")
                        continue
                elif error:
                    problems.append("No se pudo comprobar la fecha del video: " + error)
            videos.append(video)
        if len(videos) >= limit:
            break
    diagnostic = " | ".join(dict.fromkeys(problems))[:1200]
    if (returncode or problems) and not videos:
        return {"videos": [], "status": "error", "error": diagnostic or "No se pudo consultar la fuente"}
    return {"videos": videos, "status": "ok", "error": diagnostic}
