"""Register and bind GTA channels without guessing the signed-in Firefox account."""

from __future__ import annotations

import json
import configparser
import os
import re
import uuid
from pathlib import Path
from urllib.parse import urlsplit, unquote

from .store import ROOT


def normalize_handle(value: str) -> str:
    value = value.strip()
    if value.startswith(("https://", "http://")):
        parsed = urlsplit(value)
        if (parsed.hostname or "").lower() not in ("youtube.com", "www.youtube.com"):
            raise ValueError("Usa el enlace de YouTube o el handle del canal")
        value = unquote(parsed.path).strip("/").split("/")[0]
        if not value.startswith("@"):
            raise ValueError("Usa el enlace al perfil con @handle, no un video")
    value = "@" + value.lstrip("@")
    if not re.fullmatch(r"@[\w.\-]{3,30}", value, flags=re.UNICODE):
        raise ValueError("Handle de YouTube inválido")
    return value


def _path(root: Path) -> Path:
    path = root / ".mp" / "youtube.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _read(path: Path) -> dict:
    if not path.exists():
        return {"accounts": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("accounts"), list):
        raise ValueError("El catálogo de canales no tiene el formato esperado")
    return data


def register(name: str, handle: str = "", *, pending_firefox: bool = False, youtube_channel_id: str = "", root: Path = ROOT) -> dict:
    from cache import atomic_write_json, json_write_lock

    name = name.strip()
    if not name:
        raise ValueError("Escribe el nombre del canal")
    handle = normalize_handle(handle) if handle else ""
    if not handle and not pending_firefox:
        raise ValueError("Añade el handle del canal")
    if youtube_channel_id and not re.fullmatch(r"UC[\w-]{22}", youtube_channel_id):
        raise ValueError("ID de canal inválido")
    path = _path(root)
    with json_write_lock(str(path)):
        data = _read(path)
        match = next((row for row in data["accounts"] if
                      (handle and str(row.get("youtube_handle", "")).casefold() == handle.casefold())
                      or (pending_firefox and row.get("gta6_pending_identity"))), None)
        if match:
            if youtube_channel_id:
                match["youtube_channel_id"] = youtube_channel_id
                atomic_write_json(str(path), data)
            return dict(match)
        row = {
            "id": str(uuid.uuid4()), "nickname": name, "youtube_handle": handle,
            "youtube_channel_id": youtube_channel_id,
            "niche": "GTA 6: noticias oficiales, guías y clips comentados",
            "language": "español", "short_voice": "es-ES-AlvaroNeural", "long_voice": "es-ES-AlvaroNeural",
            "hook_profile": "educational", "image_style": "tarjetas editoriales originales, legibles, sin gameplay ficticio",
            "firefox_profile": "", "videos": [], "gta6_managed": True,
            "gta6_pending_identity": pending_firefox, "automation_profile_verified": False,
            "upload_disabled_reason": "Identifica el canal y confirma qué perfil de Firefox tiene su sesión antes de habilitar las subidas.",
        }
        data["accounts"].append(row)
        atomic_write_json(str(path), data)
        return row


def listing(root: Path = ROOT) -> list[dict]:
    rows = _read(_path(root))["accounts"]
    result = []
    for row in rows:
        verified = row.get("automation_profile_verified") is True
        result.append({"id": row["id"], "nickname": row.get("nickname", ""),
                       "youtube_handle": row.get("youtube_handle", ""), "managed": bool(row.get("gta6_managed")),
                       "pending_identity": bool(row.get("gta6_pending_identity")),
                       "profile_ready": verified and bool(row.get("firefox_profile")) and Path(row["firefox_profile"]).is_dir(),
                       "firefox_profile": row.get("firefox_profile", ""),
                       "upload_disabled_reason": row.get("upload_disabled_reason", "Confirma el perfil para habilitar la subida automática.") if not verified else ""})
    return result


def bind(channel_id: str, *, profile: str, confirmed: bool, name: str = "", handle: str = "", youtube_channel_id: str = "", root: Path = ROOT) -> dict:
    from cache import atomic_write_json, json_write_lock

    path = _path(root)
    profile_path = Path(profile).expanduser()
    if not confirmed or not profile_path.is_absolute() or not profile_path.is_dir():
        raise ValueError("Indica una carpeta de perfil existente y confirma que su sesión corresponde a este canal")
    current = next((r for r in _read(path)["accounts"] if r["id"] == channel_id), None)
    if current is None:
        raise LookupError("No existe ese canal")
    effective_handle = normalize_handle(handle or current.get("youtube_handle", ""))
    expected_id = youtube_channel_id or current.get("youtube_channel_id", "")
    if not expected_id or (effective_handle.casefold() != str(current.get("youtube_handle", "")).casefold() and not youtube_channel_id):
        expected_id = resolve_channel_id(effective_handle)
    if not re.fullmatch(r"UC[\w-]{22}", expected_id):
        raise ValueError("No se pudo verificar el ID público del canal")
    with json_write_lock(str(path)):
        data = _read(path)
        row = next((r for r in data["accounts"] if r["id"] == channel_id), None)
        if row is None:
            raise LookupError("No existe ese canal")
        effective_handle = normalize_handle(handle or row.get("youtube_handle", ""))
        if any(r["id"] != channel_id and str(r.get("youtube_handle", "")).casefold() == effective_handle.casefold() for r in data["accounts"]):
            raise ValueError("Ese handle ya está registrado en otro canal")
        row.update(firefox_profile=str(profile_path.resolve()), youtube_handle=effective_handle,
                   youtube_channel_id=expected_id, automation_profile_verified=True, gta6_pending_identity=False, upload_disabled_reason="")
        if name.strip():
            row["nickname"] = name.strip()
        atomic_write_json(str(path), data)
        return row


def for_generation(channel_id: str, auto_upload: bool, root: Path = ROOT) -> dict:
    row = next((r for r in _read(_path(root))["accounts"] if r["id"] == channel_id), None)
    if row is None or row.get("gta6_pending_identity"):
        raise ValueError("Selecciona un canal identificado para el lote")
    if auto_upload and (row.get("automation_profile_verified") is not True or not row.get("youtube_channel_id") or not row.get("firefox_profile") or not Path(row["firefox_profile"]).is_dir()):
        raise ValueError("Confirma primero el perfil de Firefox de este canal para activar la subida automática")
    return dict(row)


def resolve_channel_id(handle: str) -> str:
    """Read public channel metadata with the existing yt-dlp dependency, without browser credentials."""
    import yt_dlp
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True, "extract_flat": True,
                              "playlist_items": "0", "socket_timeout": 15, "retries": 0, "extractor_retries": 0}) as ydl:
            info = ydl.extract_info("https://www.youtube.com/" + normalize_handle(handle) + "/about", download=False)
        value = str((info or {}).get("channel_id") or "")
    except Exception as exc:
        raise ValueError("No se pudo consultar el ID público del canal; comprueba el handle y vuelve a intentar") from exc
    if not re.fullmatch(r"UC[\w-]{22}", value):
        raise ValueError("No se encontró un ID público para este handle")
    return value


def profiles() -> list[dict]:
    """Read profile names and paths only; never cookies, sessions or saved logins."""
    directory = Path(os.environ.get("APPDATA", "")) / "Mozilla" / "Firefox"
    parser = configparser.RawConfigParser()
    parser.read(directory / "profiles.ini", encoding="utf-8")
    rows = []
    for section in parser.sections():
        if not section.startswith("Profile") or not parser.has_option(section, "Path"):
            continue
        path = Path(parser.get(section, "Path"))
        if parser.get(section, "IsRelative", fallback="1") == "1":
            path = directory / path
        rows.append({"name": parser.get(section, "Name", fallback=section), "path": str(path.resolve()), "exists": path.is_dir()})
    return rows
