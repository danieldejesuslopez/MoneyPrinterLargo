"""Viewer-created clips from the selected Twitch and Kick creators."""
import asyncio
import json
import time
import shutil
from urllib.parse import urlsplit, parse_qs
from uuid import uuid4

from .hermes_live import channel_url, permitted, route_for, save_manifest


def clip_url(raw, creator, platform="twitch"):
    parsed = urlsplit(raw)
    host = (parsed.hostname or "").removeprefix("www.")
    parts = parsed.path.strip("/").split("/")
    if platform == "kick":
        if parsed.scheme != "https" or host != "kick.com" or not parts or parts[0].casefold() != creator.casefold():
            return ""
        slug = parts[2] if len(parts) == 3 and parts[1] == "clips" else parse_qs(parsed.query).get("clip", [""])[0]
        if slug.startswith("clip_") and all(c.isalnum() or c in "_-" for c in slug):
            return f"https://kick.com/{creator}/clips/{slug}"
        return ""
    if parsed.scheme != "https" or parsed.query or parsed.fragment:
        return ""
    parts = parsed.path.strip("/").split("/")
    host = (parsed.hostname or "").removeprefix("www.")
    if host == "twitch.tv" and len(parts) == 3 and parts[:2] == [creator, "clip"]:
        return "https://clips.twitch.tv/" + parts[2]
    if host == "clips.twitch.tv" and len(parts) == 1 and parts[0] not in {"embed", "create"}:
        return "https://clips.twitch.tv/" + parts[0]
    return ""


async def discover(channel):
    from playwright.async_api import async_playwright, TimeoutError
    creator = urlsplit(channel_url(channel)).path.strip("/")
    platform = "kick" if urlsplit(channel).hostname == "kick.com" else "twitch"
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(channel="chrome")
        try:
            page = await browser.new_page()
            page_url = f"https://kick.com/{creator}/clips" if platform == "kick" else f"https://www.twitch.tv/{creator}/clips?filter=clips&range=24hr"
            response = await page.goto(page_url,
                                       wait_until="domcontentloaded", timeout=45000)
            if not response or response.status >= 400:
                raise ValueError("No se pudo consultar la página de clips de la comunidad")
            selector = 'a[href*="/clips/clip_"],a[href*="clip=clip_"]' if platform == "kick" else 'a[href*="/clip/"],a[href*="clips.twitch.tv/"]'
            try:
                await page.locator(selector).first.wait_for(timeout=15000)
            except TimeoutError:
                text = (await page.locator("body").inner_text()).lower()
                if any(s in text for s in ("no clips", "no hay clips", "no se han encontrado")):
                    return []
                raise ValueError("La página de comunidad no mostró clips; se reintentará en el próximo ciclo")
            hrefs = await page.locator(selector).evaluate_all("nodes => nodes.map(a => a.href)")
            return list(dict.fromkeys(u for h in hrefs if (u := clip_url(h, creator, platform))))[:20]
        finally:
            await browser.close()


def validate_clip(info, creator, min_seconds=7):
    if str(info.get("channel", "")).casefold() != creator.casefold():
        raise ValueError("El clip pertenece a otro creador")
    if not any(str(c).casefold().removesuffix(" (gta)") in {"grand theft auto vi", "gta vi", "gta 6"} for c in info.get("categories", [])):
        raise ValueError("El clip no está identificado como GTA VI")
    if not min_seconds <= float(info.get("duration") or 0) <= 120:
        raise ValueError(f"El clip queda fuera del intervalo de {min_seconds} a 120 segundos")
    if not info.get("timestamp") or not 0 <= time.time() - info["timestamp"] <= 86400:
        raise ValueError("El clip no corresponde a las últimas 24 horas")


def download(url, channel, target, min_seconds=7):
    import yt_dlp
    creator = urlsplit(channel_url(channel)).path.strip("/")
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True,
                          "noprogress": True,
                          "format": "best[height<=720]/best", "outtmpl": str(target),
                          "socket_timeout": 20, "retries": 1, "fragment_retries": 1}) as ydl:
        info = ydl.extract_info(url, download=False)
        validate_clip(info, creator, min_seconds)
        ydl.process_info(info)
    return {"title": info.get("title", ""), "clipper": info.get("uploader", ""),
            "broadcaster": info.get("channel", ""), "duration": info["duration"], "clip_url": url}


async def poll(desk):
    routes = desk.workspace.store.read()["settings"].get("hermes_routes", {})
    for channel in routes:
        state = desk.workspace.store.read()
        if not state.get("hermes_enabled"):
            return
        if not permitted(state, channel):
            continue
        try:
            urls = await discover(channel)
            added = 0
            for url in urls:
                state = desk.workspace.store.read()
                if not state.get("hermes_enabled") or not permitted(state, channel):
                    return
                seen = state.get("hermes_community_seen", {}).get(url, {})
                if seen.get("status") in ("captured", "skipped") or time.time()-seen.get("at", 0) < 900:
                    continue
                target = desk.directory / (uuid4().hex + ".ts")
                if (shutil.disk_usage(desk.directory).free < 3*1024**3
                        or len(list(desk.directory.glob('*.json'))) + len(desk.active) >= 18):
                    desk.log("Comunidad en espera: límite de capturas pendientes o capacidad de disco")
                    return
                try:
                    meta = await asyncio.to_thread(download, url, channel, target, state["settings"].get("hermes_min_seconds", 7))
                    fresh = desk.workspace.store.read()
                    if not fresh.get("hermes_enabled") or not permitted(fresh, channel):
                        target.unlink(missing_ok=True)
                        return
                    save_manifest(target.with_suffix(".json"), {"url": channel, "captured_at": time.time(),
                                  "route": route_for(fresh, channel), "community": meta})
                    outcome = {"status": "captured", "at": time.time(), "recording_id": target.stem}
                    added += 1
                    desk.log("Comunidad: " + channel + " · " + meta["title"])
                except ValueError as exc:
                    target.unlink(missing_ok=True)
                    outcome = {"status": "skipped", "at": time.time(), "error": str(exc)[:200]}
                except Exception as exc:
                    target.unlink(missing_ok=True)
                    outcome = {"status": "error", "at": time.time(), "error": str(exc)[:200]}
                    desk.log("Comunidad " + channel + ": " + str(exc)[:180])
                with desk.workspace.store.edit() as current:
                    current.setdefault("hermes_community_seen", {})[url] = outcome
                if added >= 2:
                    break
            desk.log(f"Comunidad {channel}: {len(urls)} encontrados, {added} nuevos")
        except Exception as exc:
            desk.log(f"Comunidad {channel}: {exc}")


async def community_loop(desk):
    while True:
        if desk.workspace.store.read()["settings"].get("hermes_community_enabled"):
            await poll(desk)
        await asyncio.sleep(900)
