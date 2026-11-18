"""Transactional, installation-local state. No account secrets are stored here."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PLATFORMS = ("x", "instagram", "tiktok", "youtube", "threads", "bluesky")
PILLARS = ("news", "tips", "gameplay", "nostalgia", "spotlight", "countdown", "questions")
PILLAR_LABELS = {
    "news": "Noticias oficiales", "tips": "Guías y trucos", "gameplay": "Gameplay",
    "nostalgia": "Nostalgia GTA V", "spotlight": "Personajes y lugares",
    "countdown": "Cuenta atrás / resumen", "questions": "Preguntas",
}

SOURCES = [
    ("rockstar-vi", "GTA VI · sitio oficial", "https://www.rockstargames.com/VI", "official"),
    ("rockstar-news", "Rockstar Newswire", "https://www.rockstargames.com/newswire", "official"),
    ("rockstar-support", "Rockstar Support", "https://support.rockstargames.com/", "official"),
    ("rockstar-yt", "Rockstar Games · YouTube", "https://www.youtube.com/@RockstarGames", "official"),
    ("rockstar-x", "Rockstar Games · X", "https://x.com/RockstarGames", "official"),
    ("rockstar-ig", "Rockstar Games · Instagram", "https://www.instagram.com/rockstargames/", "official"),
    ("take-two", "Take-Two · inversores", "https://www.take2games.com/ir", "official"),
    ("playstation", "PlayStation · GTA VI", "https://www.playstation.com/games/grand-theft-auto-vi/", "official"),
    ("xbox", "Xbox · GTA VI", "https://www.xbox.com/games/gta-vi", "official"),
    ("reddit-gta6", "r/GTA6 · descubrir al creador original", "https://www.reddit.com/r/GTA6/", "discovery"),
]

ROUTINES = [
    ("morning-facts", "Mañana", "Revisar fuentes oficiales y hechos con más de 48 horas"),
    ("morning-queue", "Mañana", "Revisar canal, fecha, duplicados y preparar los clips del día"),
    ("midday-news", "Cada 2–3 h", "Comprobar noticias oficiales y preguntas nuevas"),
    ("engage-1", "Comunidad · mañana", "15 min: responder comentarios con información verificada"),
    ("engage-2", "Comunidad · tarde", "15 min: aportar respuestas útiles a creadores y guardar preguntas"),
    ("evening-review", "Noche", "Registrar métricas de 12/24/72 h, una lección y el test de mañana"),
]
KICKOFF = [
    "Configurar nombre, handle, bio, avatar, banner, redes y zona horaria",
    "Verificar hechos, añadir al menos 50 creadores y estudiar 3–5 páginas del nicho",
    "Preparar 15 ideas, plantilla visual y los primeros 3 clips verticales",
    "Comenzar el calendario y registrar cada publicación y su fuente",
    "Leer las primeras métricas de 24 h y probar una sola variable",
    "Preparar plantillas, lista de fuentes y cola del día de lanzamiento",
    "Revisar top 3, bottom 3 y medianas; actualizar las reglas de la próxima semana",
]


def initial_state() -> dict:
    from .autopilot import initial_state as autopilot_state

    return {
        "version": 1,
        "settings": {
            "name": "GTA 6", "handle": "", "timezone": "America/La_Paz",
            "language": "español", "platforms": ["x", "instagram", "tiktok", "youtube"],
            "angle": "Consejos útiles, momentos de gameplay y noticias verificadas",
            "bio": "Fan page de GTA 6. Noticias, clips y guías. No afiliada a Rockstar Games.",
            "voice": "", "voice_mode": "text", "avoid_creators": "", "approval_hours": "09:00–21:00",
            "daily_x": 6, "daily_video": 2, "slots": ["08:00", "10:00", "12:00", "15:00", "18:00", "21:00"],
            "buffer_channels": {}, "launch_date": "", "launch_fact_id": "",
            "kickoff_start": "", "launch_mode": False,
            "youtube_channel_id": "", "batch_auto_upload": False,
            "hermes_youtube_channel_ids": [],
            "hermes_routes": {}, "hermes_layout": "crop", "hermes_platforms": ["youtube", "tiktok", "instagram", "x"],
            "hermes_community_enabled": False,
            "hermes_min_seconds": 7, "hermes_max_seconds": 30,
        },
        "sources": [{"id": i, "name": n, "url": u, "kind": k, "credit": "@RockstarGames" if i.startswith("rockstar") else "",
                     "permission": "review", "permission_note": "", "active": True} for i, n, u, k in SOURCES],
        "facts": [], "ideas": [], "posts": [], "metrics": [], "experiments": [], "news_jobs": [],
        "lessons": [], "engagement": [], "takedowns": [], "references": [], "checks": {}, "events": [],
        "autopilot": autopilot_state(),
    }


class Store:
    def __init__(self, directory: str | Path | None = None):
        self.directory = Path(directory) if directory else ROOT / ".mp" / "gta6"

    def connect(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.directory / "workspace.sqlite3", timeout=30)
        db.execute("CREATE TABLE IF NOT EXISTS workspace (id INTEGER PRIMARY KEY, data TEXT NOT NULL)")
        db.execute("INSERT OR IGNORE INTO workspace VALUES (1, ?)", (json.dumps(initial_state(), ensure_ascii=False),))
        db.commit()
        return db

    def read(self) -> dict:
        db = self.connect()
        try:
            return self._migrate(json.loads(db.execute("SELECT data FROM workspace WHERE id = 1").fetchone()[0]))
        finally:
            db.close()

    @staticmethod
    def _migrate(data: dict) -> dict:
        for key, value in initial_state()["settings"].items():
            data.setdefault("settings", {}).setdefault(key, value)
        data.setdefault("news_jobs", [])
        from .autopilot import initial_state as autopilot_state
        defaults = autopilot_state()
        automatic = data.setdefault("autopilot", {})
        for key, value in defaults.items():
            automatic.setdefault(key, value)
        for key, value in defaults["config"].items():
            automatic["config"].setdefault(key, value)
        return data

    @contextmanager
    def edit(self):
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            state = self._migrate(json.loads(db.execute("SELECT data FROM workspace WHERE id = 1").fetchone()[0]))
            yield state
            db.execute("UPDATE workspace SET data = ? WHERE id = 1", (json.dumps(state, ensure_ascii=False, allow_nan=False),))
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()
