"""Dedicated event loop for recording on Windows (also survives closing the web UI)."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gta6 import Workspace
from gta6.hermes_live import Desk

if __name__ == "__main__":
    desk = Desk(Workspace(sys.argv[1] if len(sys.argv) > 1 else None))
    desk.directory.mkdir(parents=True, exist_ok=True)
    asyncio.run(desk.run())
