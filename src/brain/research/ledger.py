"""Every experiment ever tried, and what became of it.

The ledger does two jobs, and the second matters more than the first.

  - **Memory for the advisor.** Claude starts every round with no recollection
    of the last one. Feeding it the history is what stops it proposing the
    same dead end three rounds running.
  - **A count of forking paths.** The confirmation era is small. Each new
    experiment judged against the same era is another draw from the same
    lottery, and after enough draws something passes by luck. The ledger
    counts how many experiments a given era has already absorbed, so the
    loop can say when its own verdicts have stopped meaning anything — until
    new videos move the era forward.

Append-only JSONL. A research round is a historical fact, including the ideas
that failed.
"""

from __future__ import annotations

import json
import os
from typing import Any

from ..config import cache_file

LEDGER_FILE = "research_ledger.jsonl"

#: Experiments one confirmation era may absorb before its verdicts are
#: treated as exhausted. At fourteen confirmation videos and a 0.8 bar, the
#: chance of at least one false "recommended" climbs past a coin flip well
#: before this, so the number is a ceiling, not a comfortable budget.
ERA_BUDGET = 12


def ledger_path() -> str:
    return cache_file(LEDGER_FILE)


def record_round(entry: dict[str, Any]) -> None:
    with open(ledger_path(), "a", encoding="utf-8") as file:
        file.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")


def load_rounds() -> list[dict[str, Any]]:
    path = ledger_path()
    if not os.path.exists(path):
        return []
    rounds: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                rounds.append(entry)
    return rounds


def canonical(changes: dict[str, Any]) -> str:
    """Order-independent identity of a change set, for duplicate detection."""
    normalized = {
        key: sorted(value) if isinstance(value, list) else value
        for key, value in (changes or {}).items()
    }
    return json.dumps(normalized, sort_keys=True, ensure_ascii=False)


def tried_change_sets() -> set[str]:
    return {
        canonical(result.get("changes") or {})
        for entry in load_rounds()
        for result in entry.get("results") or []
        if result.get("changes")
    }


def experiments_on_era(era: str) -> int:
    """How many experiments have already been judged on this confirmation era."""
    if not era:
        return 0
    return sum(
        1
        for entry in load_rounds()
        for result in entry.get("results") or []
        if ((result.get("comparison") or {}).get("confirm") or {}).get("era") == era
    )


def history(limit: int = 40) -> list[dict[str, Any]]:
    """Compact past experiments, newest last, for the advisor's dossier."""
    rows: list[dict[str, Any]] = []
    for entry in load_rounds():
        for result in entry.get("results") or []:
            comparison = result.get("comparison") or {}
            rows.append({
                "round": entry.get("round_id"),
                "name": result.get("name"),
                "changes": result.get("changes"),
                "hypothesis": result.get("hypothesis"),
                "verdict": comparison.get("verdict") or result.get("verdict"),
                "search_delta_pp": (comparison.get("search") or {}).get("delta_pp"),
                "confirm_delta_pp": (comparison.get("confirm") or {}).get("delta_pp"),
                "confirm_probability": (comparison.get("confirm") or {}).get(
                    "probability_better"
                ),
            })
    return rows[-limit:]


def find(round_id: str, name: str) -> dict[str, Any] | None:
    for entry in load_rounds():
        if entry.get("round_id") != round_id:
            continue
        for result in entry.get("results") or []:
            if result.get("name") == name:
                return {**result, "round_id": round_id}
    return None


def latest_recommended() -> list[dict[str, Any]]:
    """Recommended experiments from the most recent round that produced any."""
    for entry in reversed(load_rounds()):
        picks = [
            {**result, "round_id": entry.get("round_id")}
            for result in entry.get("results") or []
            if (result.get("comparison") or {}).get("verdict") == "recommended"
        ]
        if picks:
            return picks
    return []
