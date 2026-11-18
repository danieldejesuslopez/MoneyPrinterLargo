"""Local workers for the Hermes launch desk. Start/stop intent survives a backend restart."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
from urllib.parse import urlsplit
from uuid import uuid4

from clips.store import file_lock
from . import clipping
from .autopilot import _avoided
from .store import ROOT

PLATFORMS = ("youtube", "twitch", "kick")


def channel_url(raw):
    try:
        p = urlsplit(raw)
        if p.scheme != "https" or p.username or p.password or p.port or p.query:
            return ""
        host = (p.hostname or "").removeprefix("www.")
    except (TypeError, ValueError):
        return ""
    path = p.path.strip("/")
    if host == "youtube.com" and re.fullmatch(r"(@[\w.-]+|channel/[\w-]+)(/live)?", path):
        return "https://www.youtube.com/" + path.removesuffix("/live") + "/live"
    if host in ("twitch.tv", "kick.com") and re.fullmatch(r"[\w-]{2,50}", path, re.ASCII):
        if path.lower() not in {"directory", "search", "videos", "browse", "categories", "settings"}:
            return "https://" + host + "/" + path.lower()
    return ""


def save_manifest(path, data):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data), encoding="utf-8")
    temp.replace(path)


def configured(state):
    channels = {}
    excluded = set()
    for source in state["sources"]:
        urls = {channel_url(source.get(k, "")) for k in ("url", "monitor_url")} - {""}
        excluded.update(u for u in urls if not permitted(state, u))
        urls = {u for u in urls if permitted(state, u)}
        if not source.get("active") or not source.get("monitor_enabled", True) or source.get("permission") == "denied" or _avoided(source, state):
            excluded.update(urls)
        else:
            channels.update(dict.fromkeys(urls, source))
    return channels, excluded


def route_for(state, url):
    return state["settings"].get("hermes_routes", {}).get(channel_url(url), {})


def permitted(state, url):
    routes = state["settings"].get("hermes_routes", {})
    return not routes or channel_url(url) in routes


def captions_ready(manifest, route, moment=None):
    if not route:
        return True
    try:
        return (manifest.with_suffix(".caption-language").read_text(encoding="utf-8") == route["language"]
                and manifest.with_suffix(".caption-layout").read_text(encoding="utf-8") == "separate-text-v2"
                and (moment is None or json.loads(manifest.with_suffix(".caption-span").read_text(encoding="utf-8")) == [moment["start"], moment["end"]])
                and manifest.with_suffix(".caption-model").read_text(encoding="utf-8") == route.get("whisper_model", "small"))
    except (OSError, ValueError):
        return False


def agent_command():
    if cmd := shutil.which("hermes"):
        return cmd
    home = Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "hermes"
    for path in (Path.home() / ".local/bin/hermes.cmd", home / "bin/hermes.cmd", home / "bin/hermes.exe"):
        if path.is_file():
            return str(path)
    raise ValueError("No se encontró la CLI de Hermes en esta instalación")


async def command(args, timeout, prompt=None):
    process = await asyncio.create_subprocess_exec(*map(str, args), cwd=ROOT,
        stdin=asyncio.subprocess.PIPE if prompt else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"}, creationflags=0x08000000 if os.name == "nt" else 0)
    try:
        out, err = await asyncio.wait_for(process.communicate(prompt.encode() if prompt else None), timeout)
    finally:
        if process.returncode is None:
            if os.name == "nt":
                killer = await asyncio.create_subprocess_exec("taskkill", "/PID", str(process.pid), "/T", "/F",
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                await killer.wait()
            else:
                process.kill()
            await process.wait()
    if process.returncode:
        message = (err or out).decode("utf-8", "replace").strip().splitlines()
        raise ValueError((message[-1] if message else "Falló " + str(args[0]))[:250])
    return out.decode("utf-8", "replace").strip()


async def agent(prompt, sheet=None):
    args = [agent_command(), "chat", "-Q", "-s", "moneyprinter-live", "-t",
            "skills" if sheet else "browser", "--max-turns", "4", "--run-budget", "90", "--query-file", "-"]
    if sheet:
        args += ["--image", str(sheet)]
    try:
        answer = await command(args, 120, prompt)
    except ValueError as exc:
        if "Anthropic credentials are rate-limited for claude-sonnet" not in str(exc):
            raise
        # The local account reports its other Claude models are still available.
        answer = await command([*args, "--model", "claude-haiku-4-5"], 120, prompt)
    return parse_agent_answer(answer)


def parse_agent_answer(answer):
    # Quiet Hermes versions may append their session receipt after the JSON.
    answer = re.sub(r"\n\s*session_id: [\w-]+\s*$", "", answer.strip())
    answer = re.sub(r"^\x60\x60\x60(?:json)?\s*|\s*\x60\x60\x60$", "", answer.strip())
    return json.loads(answer)


class Desk:
    def __init__(self, workspace):
        self.workspace = workspace
        self.directory = workspace.store.directory / "hermes"
        self.events = []
        self.active = {}
        self.discovered = {}
        self.last = {}
        self.previewing = ""

    def log(self, msg):
        self.events = [str(msg)[:380], *self.events[:39]]

    def status(self):
        state = self.workspace.store.read()
        receipts = [{k: p.get(k, "") for k in ("platform", "status", "published_url", "title", "hermes_note")}
                    for p in state["posts"] if p.get("hermes_delivery")]
        moments = []
        for manifest in self.directory.glob("*.json"):
            try:
                record = json.loads(manifest.read_text(encoding="utf-8"))
                if record.get("moment") and permitted(state, record.get("url", "")):
                    route = route_for(state, record["url"])
                    ready = captions_ready(manifest, route, record["moment"])
                    moments.append({**record["moment"], "id": manifest.stem, "url": record["url"],
                                    "preview_ready": manifest.with_suffix(".mp4").is_file() and manifest.with_suffix(".portrait").is_file() and ready,
                                    "subtitle_language": route.get("language", ""),
                                    "captured_at": record.get("captured_at", 0)})
            except (OSError, ValueError):
                continue
        return {"enabled": bool(state.get("hermes_enabled")),
                "active": list(self.active), "discovered": len(self.discovered),
                "previewing": self.previewing,
                "moments": sorted(moments, key=lambda m: (m["preview_ready"], m["captured_at"]), reverse=True)[:50],
                "events": self.events, "deliveries": receipts[-60:][::-1]}

    async def discover(self, platform):
        from .hermes_directory import directory
        while True:
            try:
                if self.workspace.store.read()["settings"].get("hermes_routes"):
                    await asyncio.sleep(300)
                    continue
                urls = await directory(platform)
                self.discovered.update(dict.fromkeys(urls, time.time()))
                self.log(f"{platform}: {len(urls)} canales encontrados")
            except Exception as exc:
                self.log(f"Descubrimiento {platform}: {exc}")
            await asyncio.sleep(300)

    async def capture(self, url):
        if not permitted(self.workspace.store.read(), url):
            return
        path = self.directory / (uuid4().hex + ".ts")
        try:
            exe = Path(sys.executable).with_name("streamlink.exe" if os.name == "nt" else "streamlink")
            await command([str(exe) if exe.exists() else "streamlink", "--stream-segmented-duration", "120",
                           "--loglevel", "error", "--output", path, url, "720p,480p,best"], 180)
            if not path.is_file() or path.stat().st_size < 16384:
                raise ValueError("Sin señal utilizable")
            state = self.workspace.store.read()
            if not permitted(state, url):
                return
            save_manifest(path.with_suffix(".json"), {"url": url, "captured_at": time.time(), "route": route_for(state, url)})
            self.log("Capturado: " + url)
        except Exception as exc:
            self.log(f"{url}: {exc}")
        finally:
            if not path.with_suffix(".json").exists():
                path.unlink(missing_ok=True)
            self.last[url] = time.time()
            self.active.pop(url, None)

    async def select(self, manifest):
        meta = json.loads(manifest.read_text(encoding="utf-8"))
        state = self.workspace.store.read()
        if not permitted(state, meta["url"]):
            return
        language = route_for(state, meta["url"]).get("language", "es")
        limit = min(120, int(meta.get("community", {}).get("duration", 120)))
        low = state["settings"].get("hermes_min_seconds", 7)
        high = min(limit, state["settings"].get("hermes_max_seconds", 30))
        if limit < low:
            return
        path = manifest.with_suffix(".ts")
        sheet = manifest.with_suffix(".jpg")
        await command(["ffmpeg", "-loglevel", "error", "-y", "-i", path, "-vf",
                       "setpts=PTS-STARTPTS,fps=1/4:start_time=0,scale=320:-2,tile=5x6", "-frames:v", "1", sheet], 60)
        decision = await agent('Review this contact sheet. Return null or {"start":int,"end":int,"title":str,"commentary":str}. '
                               f'Select a complete GTA VI moment lasting {low}–{high} seconds, between 0 and {limit} seconds. '
                               f'Write title and commentary in {"English" if language == "en" else "Spanish"}. '
                               'Only describe what is visible; do not invent dialogue or interpret routine driving as a chase.', sheet)
        if decision is None:
            return
        a, b = decision["start"], decision["end"]
        if not (type(a) is int and type(b) is int and 0 <= a < b <= limit and low <= b-a <= high):
            raise ValueError("Hermes devolvió tiempos inválidos")
        if not all(isinstance(decision.get(k), str) and decision[k].strip() for k in ("title", "commentary")):
            raise ValueError("Falta el título o comentario del momento")
        return decision

    async def render(self, manifest, source, meta):
        path = manifest.with_suffix(".ts")
        decision = meta["moment"]
        a, b = decision["start"], decision["end"]
        state = self.workspace.store.read()
        if not permitted(state, meta["url"]):
            return
        route = route_for(state, meta["url"])
        data = {"source_id": source["id"], "target": str(path), "mode": "cut", "layout": state["settings"].get("hermes_layout", "crop"), "spans": [f"{a}-{b}"],
                "titles": [decision["title"]], "commentary": decision["commentary"], "platform": "youtube"}
        if meta.get("community"):
            community = meta["community"]
            data["commentary"] += f"\n\nClip: {community['clip_url']}\nClipper: {community.get('clipper', '')}"
        if route:
            from .hermes_previews import captioned_preview
            if not captions_ready(manifest, route, decision):
                await asyncio.to_thread(captioned_preview, manifest, decision, route)
            data.update(language=route["language"], captions=str(manifest.with_suffix(".srt")),
                        hermes_url=meta["url"], hermes_youtube_channel_id=route["youtube_channel_id"])
        job = clipping.request(self.workspace, data)
        meta["job"] = job["id"]
        save_manifest(manifest, meta)
        await self.finish_job(job)

    async def finish_job(self, job):
        if job["status"] == "queued":
            await command([sys.executable, "scripts/gta6.py", "clips", "--request-id", job["id"]], 1800)
        from .hermes_publish import prepare
        prepare(self.workspace, job["id"])
        self.log("Clip procesado: " + job["id"])

    async def reviews(self):
        while True:
            for m in sorted(self.directory.glob("*.json"), key=lambda p: p.stat().st_mtime):
                try:
                    state = self.workspace.store.read()
                    channels, excluded = configured(state)
                    meta = json.loads(m.read_text(encoding="utf-8"))
                    source = channels.get(meta["url"], {})
                    expired = time.time() - meta.get("captured_at", m.stat().st_mtime) > 21600
                    if meta["url"] not in excluded and permitted(state, meta["url"]) and not expired:
                        if "moment" not in meta:
                            meta["moment"] = await self.select(m)
                            if not permitted(self.workspace.store.read(), meta["url"]):
                                continue
                            save_manifest(m, meta)
                            if meta["moment"]:
                                self.log(f"{meta['url']} · {meta['moment']['title']}")
                        if meta["moment"]:
                            if not (source.get("permission") in ("own", "granted") and source.get("permission_note") and source.get("credit")):
                                continue
                            if meta.get("job"):
                                job = next(j for j in state["clip_jobs"] if j["id"] == meta["job"])
                                await self.finish_job(job)
                            else:
                                await self.render(m, source, meta)
                    for ext in (".jpg", ".ts", ".mp4", ".portrait", ".json"):
                        m.with_suffix(ext).unlink(missing_ok=True)
                except Exception as exc:
                    self.log(f"Revisión {m.stem}: {exc}")
                    if time.time()-m.stat().st_mtime > 21600:
                        for ext in (".jpg", ".ts", ".mp4", ".portrait", ".json"):
                            m.with_suffix(ext).unlink(missing_ok=True)
                    await asyncio.sleep(10)
            await asyncio.sleep(5)

    async def run(self):
        children = []
        try:
            with file_lock(str(self.directory / "worker.lock"), timeout=0):
                from .hermes_publish import publish_loop
                from .hermes_previews import preview_loop
                from .hermes_community import community_loop
                for orphan in self.directory.glob("*.ts"):
                    if not orphan.with_suffix(".json").exists():
                        orphan.unlink(missing_ok=True)
                children = [] if self.workspace.store.read()["settings"].get("hermes_routes") else [asyncio.create_task(self.discover(p)) for p in PLATFORMS]
                children += [asyncio.create_task(self.reviews()), asyncio.create_task(preview_loop(self)),
                             asyncio.create_task(publish_loop(self)), asyncio.create_task(community_loop(self))]
                while self.workspace.store.read().get("hermes_enabled"):
                    state = self.workspace.store.read()
                    snapshot = self.status()
                    snapshot.update(running=True, heartbeat=time.time())
                    pending = self.directory / "status.tmp"
                    pending.write_text(json.dumps(snapshot), encoding="utf-8")
                    pending.replace(self.directory / "status.state")
                    channels, excluded = configured(state)
                    for url in list(self.active):
                        if not permitted(state, url):
                            self.active[url].cancel()
                    self.discovered = {u: t for u,t in self.discovered.items() if time.time()-t < 1800 and permitted(state, u)}
                    urls = (channels.keys() | self.discovered.keys()) - excluded - self.active.keys()
                    slots = min(32, max(1, int(os.getenv("MP_HERMES_RECORDERS", "8")))) - len(self.active)
                    if state["settings"].get("hermes_routes"):
                        slots = min(slots, 18 - len(list(self.directory.glob("*.json"))) - len(self.active))
                    files = [p for p in self.directory.iterdir() if p.suffix in {".ts", ".mp4"}]
                    if shutil.disk_usage(self.directory).free < 3*1024**3 or sum(p.stat().st_size for p in files if p.exists()) > 12*1024**3:
                        self.log("Capacidad de disco alcanzada; capturas en espera")
                    else:
                        for url in sorted(urls, key=lambda u: (self.last.get(u, 0), u not in self.discovered, u))[:max(0, slots)]:
                            if time.time()-self.last.get(url, 0) > 30:
                                self.active[url] = asyncio.create_task(self.capture(url))
                    await asyncio.sleep(4)
        except Exception as exc:
            self.log(f"Supervisor detenido: {exc}")
        finally:
            tasks = children + list(self.active.values())
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
