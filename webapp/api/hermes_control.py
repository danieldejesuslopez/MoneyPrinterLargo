"""Local control surface for the independent live recording process."""
import json
import asyncio
import os
import subprocess
import sys
import time
from urllib.parse import urlsplit

from fastapi import HTTPException
from gta6.hermes_live import agent_command
from gta6.store import ROOT


def status(workspace):
    state = workspace.store.read()
    enabled = bool(state.get("hermes_enabled"))
    try:
        snap = json.loads((workspace.store.directory / "hermes/status.state").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        snap = {}
    live = enabled and time.time()-snap.get("heartbeat", 0) < 20
    from gta6.channels import listing
    from gta6.hermes_live import permitted
    destinations = state["settings"].get("hermes_youtube_channel_ids", [])
    accounts = {c["id"]: c for c in listing()}
    distribution = [{"id": c, "name": accounts.get(c, {}).get("nickname", c),
                     "profile_ready": accounts.get(c, {}).get("profile_ready", False),
                     "browser": "chrome" if any(p.get("hermes_youtube_channel_id") == c and p.get("publication_browser") == "chrome"
                                                for p in state["posts"]) else "firefox",
                     "assigned_clips": sum(p.get("platform") == "youtube" and p.get("hermes_youtube_channel_id") == c
                                           for p in state["posts"])} for c in destinations]
    routes = state["settings"].get("hermes_routes", {})
    selected = [{"url": u, "creator": u.rsplit("/", 1)[-1], "language": r["language"],
                 "account": accounts.get(r["youtube_channel_id"], {}).get("nickname", r["youtube_channel_id"])} for u, r in routes.items()]
    return {**snap, "enabled": enabled, "running": live,
            "active": [u for u in snap.get("active", []) if permitted(state, u)] if live else [],
            "moments": [m for m in snap.get("moments", []) if permitted(state, m.get("url", ""))],
            "youtube_accounts": distribution, "selected_creators": selected, "restricted": bool(routes),
            "layout": state["settings"].get("hermes_layout", "crop"),
            "community_enabled": bool(state["settings"].get("hermes_community_enabled")),
            "pause_reason": state.get("hermes_pause_reason", "") if not enabled else "",
            "community_captured": sum(v.get("status") == "captured" for v in state.get("hermes_community_seen", {}).values())}


def launch(workspace, force=False):
    if not force and status(workspace)["running"]:
        return
    agent_command()
    python = ROOT / ("venv/Scripts/python.exe" if os.name == "nt" else "venv/bin/python")
    subprocess.Popen([str(python) if python.exists() else sys.executable, str(ROOT / "scripts/hermes_live.py"),
                      str(workspace.store.directory.resolve())],
        cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)


def toggle(workspace, enabled, request):
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).hostname not in ("localhost", "127.0.0.1", "::1"):
        raise HTTPException(403, "Usa la consola local de MoneyPrinter")
    if enabled:
        agent_command()
    with workspace.store.edit() as state:
        was_enabled = state.get("hermes_enabled", False)
        state["hermes_enabled"] = enabled
        if enabled:
            state.pop("hermes_pause_reason", None)
    if enabled:
        try:
            launch(workspace, force=not was_enabled)
        except Exception:
            with workspace.store.edit() as state:
                state["hermes_enabled"] = False
            raise
    return status(workspace)


async def supervise(workspace):
    while True:
        try:
            current = await asyncio.to_thread(status, workspace)
            if current["enabled"] and not current["running"]:
                await asyncio.to_thread(launch, workspace)
        except (OSError, ValueError) as exc:
            print(f"[Hermes] {exc}", flush=True)
        await asyncio.sleep(20)
