"""Dispatch the watchlist worker while the API is running, without blocking it."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from . import editorial as ed


class AutopilotScheduler:
    def __init__(self, workspace, spawn_job):
        self.workspace = workspace
        self.spawn_job = spawn_job
        self._task = None
        self._job = None

    def dispatch(self, *, produce=True, scheduled=False):
        from clips.store import file_lock

        directory = self.workspace.store.directory
        directory.mkdir(parents=True, exist_ok=True)
        with file_lock(str(directory / "autopilot-dispatch.lock"), timeout=0):
            if self._job is not None and self._job.status == "running":
                raise ValueError("El productor automático ya tiene un ciclo en marcha")
            # A separate API worker or CLI process may own the production lock.
            with file_lock(str(directory / "autopilot-run.lock"), timeout=0):
                with self.workspace.store.edit() as state:
                    auto = state["autopilot"]
                    config = auto["config"]
                    current = ed.now()
                    reserved = auto.get("dispatch_until")
                    if reserved and ed.parse_time(reserved) > current:
                        raise ValueError("Ya hay un ciclo automático iniciándose")
                    if scheduled:
                        if not config["enabled"]:
                            return None
                        due = auto.get("next_scan_at")
                        if due and ed.parse_time(due) > current:
                            return None
                    auto["dispatch_until"] = (current + timedelta(minutes=2)).isoformat()
                    auto["next_scan_at"] = (current + timedelta(minutes=config["interval_minutes"])).isoformat()
            try:
                args = ["gta6", "autopilot"]
                if not produce:
                    args.append("--scan-only")
                self._job = self.spawn_job(args, title="GTA 6 · productor automático")
            except Exception as exc:
                with self.workspace.store.edit() as state:
                    auto = state["autopilot"]
                    auto["dispatch_until"] = ""
                    auto["last_error"] = f"No se pudo iniciar el ciclo: {str(exc)[:300]}"
                    auto["next_scan_at"] = (ed.now() + timedelta(minutes=5)).isoformat()
                raise
            with self.workspace.store.edit() as state:
                state["autopilot"]["dispatch_job_id"] = self._job.id
            return self._job

    def tick(self):
        auto = self.workspace.store.read()["autopilot"]
        if not auto["config"]["enabled"]:
            return None
        if self._job is not None and self._job.status == "running":
            return None
        due = auto.get("next_scan_at")
        if due and ed.parse_time(due) > ed.now():
            return None
        try:
            return self.dispatch(scheduled=True)
        except (ValueError, BlockingIOError):
            return None

    def start(self):
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def _loop(self):
        while True:
            try:
                await asyncio.to_thread(self.tick)
            except Exception as exc:
                # A provider/process error must not silently stop future cycles.
                print(f"[gta6] Ciclo automático: {type(exc).__name__}: {str(exc)[:300]}", flush=True)
            await asyncio.sleep(15)

    async def stop(self):
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
