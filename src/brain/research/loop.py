"""One research round: measure, brief, ask, test, record. Never adopt.

    brain research               run a round (asks Claude)
    brain research --dry-run     build the dossier and prompt, ask nothing
    brain research --history     every experiment ever tried
    brain research --apply R:N   adopt a recommended experiment's knob values
    brain research --revert      go back to the code defaults

A round changes nothing about how Brain behaves. The only way an idea becomes
part of the model is `--apply`, run by a person, which writes the knob values
into `config.json`. Even then the next `brain train` still has to beat the
current champion through the promotion gate. So a bad suggestion has to get
past four independent checks before it can matter: the knob menu, the search
era, the confirmation era, and champion/challenger.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from typing import Any, Callable

from ..config import config_path
from ..training.backtest import PREDICTIVE_WARMUP
from . import advisor, bench, dossier, knobs, ledger

Progress = Callable[[str], None]


def prepare(
    warmup: int = PREDICTIVE_WARMUP,
    stride: int = 1,
    progress: Progress = print,
) -> tuple[list[dict[str, Any]], bench.Evaluation]:
    """Rows and the current configuration's walk-forward, computed once."""
    approved, _ = knobs.approved_overrides()
    progress("  construyendo filas con la configuracion actual...")
    with knobs.applied(approved):
        base_rows = bench.own_rows()
    progress("  midiendo la configuracion actual (walk-forward)...")
    current = bench.evaluate({}, base_rows, warmup=warmup, stride=stride)
    return base_rows, current


def _era_of(current: bench.Evaluation) -> str:
    pairs = [(i, i) for i in range(current.scored)]
    cut = int(round(len(pairs) * bench.SEARCH_SHARE))
    return bench.era_signature(pairs[cut:], current)


def run_round(
    ask: advisor.Ask | None = None,
    max_experiments: int = 4,
    objective: str = "format",
    dry_run: bool = False,
    warmup: int = PREDICTIVE_WARMUP,
    stride: int = 1,
    progress: Progress = print,
) -> dict[str, Any]:
    if objective not in bench.OBJECTIVES:
        raise ValueError(f"objective must be one of {bench.OBJECTIVES}")

    base_rows, current = prepare(warmup, stride, progress)
    if current.scored < 10:
        return {
            "ran": False,
            "reason": (
                f"only {current.scored} videos could be scored; a research round "
                "needs at least 10 to split into search and confirmation"
            ),
        }

    era = _era_of(current)
    used = ledger.experiments_on_era(era)
    remaining = max(0, ledger.ERA_BUDGET - used)
    allowed = min(max_experiments, remaining)

    brief = dossier.build_dossier(base_rows, current, objective, max(allowed, 0))
    prompt = advisor.build_prompt(brief, max(allowed, 0))
    if dry_run:
        return {"ran": False, "dry_run": True, "prompt": prompt, "dossier": brief}

    progress("  consultando a Claude (sin herramientas, solo texto)...")
    ask = ask or advisor.ask_claude
    raw = ask(prompt)
    proposals = advisor.extract_proposals(advisor.parse_response(raw), allowed)

    round_id = "r" + _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d%H%M%S")
    results: list[dict[str, Any]] = []

    if remaining <= 0:
        progress("  la era de confirmacion esta agotada: no se evalua nada")
    for number, experiment in enumerate(proposals["experiments"], start=1):
        progress(
            f"  [{number}/{len(proposals['experiments'])}] {experiment['name']} "
            f"{experiment['changes']}"
        )
        try:
            candidate = bench.evaluate(
                experiment["changes"], base_rows, warmup=warmup, stride=stride
            )
            comparison = bench.compare(current, candidate, objective)
            overall = bench.summarize(candidate)
        except Exception as error:  # one broken experiment must not sink the round
            comparison = {"verdict": "error", "reason": str(error)}
            overall = {}
        results.append({**experiment, "overall": overall, "comparison": comparison})

    entry = {
        "round_id": round_id,
        "created_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "objective": objective,
        "advisor_model": advisor.research_settings()["model"] or "cli-default",
        "current": bench.summarize(current),
        "approved_before": knobs.approved_overrides()[0],
        "era": era,
        "era_experiments_before": used,
        "era_budget": ledger.ERA_BUDGET,
        "diagnosis": proposals["diagnosis"],
        "results": results,
        "rejected": proposals["rejected"],
        "feature_ideas": proposals["feature_ideas"],
        "data_priorities": proposals["data_priorities"],
        "raw_answer_chars": len(raw or ""),
    }
    ledger.record_round(entry)
    return {"ran": True, **entry}


# -- adopting an experiment ----------------------------------------------------


def _read_config_file() -> dict[str, Any]:
    path = config_path()
    if not os.path.exists(path):
        raise FileNotFoundError(
            "config.json does not exist; configure the application first "
            "(cp config.example.json config.json)"
        )
    with open(path, "r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError("config.json is not a JSON object")
    return data


def _write_config_file(data: dict[str, Any]) -> None:
    path = config_path()
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4, ensure_ascii=False)
        file.write("\n")
    os.replace(tmp_path, path)


def apply(reference: str, force: bool = False) -> dict[str, Any]:
    """Adopt one experiment's knob values. `reference` is "round_id:name"."""
    if ":" not in reference:
        raise ValueError("use ROUND_ID:NAME, as printed by `brain research`")
    round_id, name = reference.split(":", 1)
    result = ledger.find(round_id.strip(), name.strip())
    if result is None:
        raise LookupError(f"no experiment {name!r} in round {round_id!r}")

    verdict = (result.get("comparison") or {}).get("verdict")
    if verdict != "recommended" and not force:
        raise PermissionError(
            f"{name} was {verdict!r}, not recommended; pass --force to adopt it anyway"
        )

    changes, errors = knobs.validate(result.get("changes") or {})
    if errors:
        raise ValueError("; ".join(errors))

    data = _read_config_file()
    brain_section = data.setdefault("brain", {})
    if not isinstance(brain_section, dict):
        raise ValueError("config.json 'brain' is not an object")
    previous = dict(brain_section.get("training") or {})
    brain_section["training"] = {**previous, **changes}
    _write_config_file(data)

    steps = []
    if knobs.needs_rebuild(changes):
        steps.append("python scripts/brain.py dataset")
    steps.append("python scripts/brain.py train")
    return {
        "applied": changes,
        "previous": previous,
        "now": brain_section["training"],
        "forced": bool(force and verdict != "recommended"),
        "next_steps": steps,
    }


def revert() -> dict[str, Any]:
    """Remove every adopted knob value; Brain returns to its code defaults."""
    data = _read_config_file()
    brain_section = data.get("brain")
    previous = {}
    if isinstance(brain_section, dict) and "training" in brain_section:
        previous = dict(brain_section.pop("training") or {})
        _write_config_file(data)
    return {
        "removed": previous,
        "next_steps": (
            ["python scripts/brain.py dataset", "python scripts/brain.py train"]
            if previous else []
        ),
    }
