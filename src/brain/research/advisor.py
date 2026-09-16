"""Asking Claude for research advice, and refusing anything unsafe it says.

The call goes through the locally authenticated Claude CLI — the same
`claude --print` path `LearningCoach` already uses — with every tool disabled:
the advisor can read the dossier and write text, and nothing else. It cannot
open files, run commands or touch the repository.

Its answer is then treated as untrusted input. Experiments that name an
unknown knob, carry an out-of-range value, repeat something already tried or
exceed the per-round cap are dropped with the reason recorded. Free-form
feature ideas are kept for a human to read and are never executed.

The CLI is invoked directly rather than through `llm_provider`, which imports
the application config and would make Brain crash wherever `config.json`
is missing — a property the whole package is built to avoid.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Any, Callable

from ..config import brain_config, read_config, root_dir
from . import knobs, ledger

Ask = Callable[[str], str]

DEFAULT_TIMEOUT_SECONDS = 900

# Plain ASCII, no quotes or shell metacharacters: Windows refuses to pass
# some characters to a .cmd shim, and this string travels as an argument.
SYSTEM_PROMPT = (
    "You are a careful machine learning research advisor for a small-data "
    "prediction system. You reason from the evidence you are given, you know "
    "that small samples produce convincing noise, and you would rather "
    "propose nothing than propose a variation that only fits the test. "
    "Return only one JSON object, with no prose before or after it."
)


def build_prompt(dossier: dict[str, Any], max_experiments: int) -> str:
    return f"""MoneyPrinter Brain is asking how to improve itself.

It predicts how a YouTube video will perform relative to its own channel,
before the video is made. Below is a dossier of its current state, measured
by a walk-forward backtest. Read it, then propose experiments.

HOW YOUR PROPOSALS WILL BE JUDGED
- Each experiment is a set of knob values. It is run through the real
  training code and compared with the current configuration on the same
  videos: first on an older search era, then on a newer confirmation era it
  never saw. It is recommended only if it gains at least
  {dossier["evaluation_protocol"]["min_search_gain_pp"]} percentage points on
  search AND is better on confirmation with paired-bootstrap probability of at
  least {dossier["evaluation_protocol"]["min_confirm_probability"]}.
- The confirmation era has absorbed
  {dossier["evaluation_protocol"]["confirmation_era_experiments_used"]} of a
  budget of {dossier["evaluation_protocol"]["confirmation_era_budget"]}
  experiments. Every proposal spends one. Do not propose sweeps of a single
  knob, or several near-identical variations: that is how noise gets
  recommended.

RULES
1. Use only knobs listed under "knobs", with values inside their range. Any
   other key is rejected automatically.
2. Propose at most {max_experiments} experiments. Fewer is better than
   padding. An empty list is a valid answer if nothing is worth the budget.
3. Never repeat a change set that appears in "history".
4. Every hypothesis must point at specific evidence in the dossier.
5. Ideas the knobs cannot express go in "feature_ideas". A human reads them;
   they are never run. The history shows that re-encoding existing features
   has not helped, so favour ideas that add new information.
6. Write "diagnosis", "hypothesis", "rationale" and "data_priorities" in
   Spanish. Keep "name" as a short snake_case slug.

RESPOND WITH EXACTLY THIS JSON SHAPE
{{
  "diagnosis": "what the evidence says is limiting the model",
  "experiments": [
    {{"name": "slug", "hypothesis": "why, citing the dossier",
      "changes": {{"knob_name": value}}}}
  ],
  "feature_ideas": [
    {{"name": "slug", "rationale": "why", "data_needed": "what to collect"}}
  ],
  "data_priorities": ["most valuable data to start collecting, in order"]
}}

DOSSIER
{json.dumps(dossier, ensure_ascii=False, indent=1, default=str)}
"""


# -- calling Claude ----------------------------------------------------------


def resolve_cli() -> str:
    """Find the Claude CLI, including npm's `.cmd` shim on Windows."""
    configured = os.environ.get("MP_CLAUDE_CLI_COMMAND", "").strip()
    if not configured:
        configured = str(read_config().get("claude_cli_command") or "claude").strip()

    found = shutil.which(configured)
    if found:
        return found
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        for name in ("claude.cmd", "claude.exe", "claude"):
            candidate = os.path.join(appdata, "npm", name)
            if os.path.exists(candidate):
                return candidate
    return configured


def research_settings() -> dict[str, Any]:
    section = brain_config().get("research")
    section = section if isinstance(section, dict) else {}
    try:
        timeout = int(section.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT_SECONDS
    return {"model": str(section.get("model") or "").strip(), "timeout": timeout}


def ask_claude(prompt: str, model: str = "", timeout: int | None = None) -> str:
    """One non-interactive, tool-less Claude call. Raises on failure."""
    settings = research_settings()
    model = model or settings["model"]
    timeout = timeout or settings["timeout"]

    args = [
        resolve_cli(),
        "--print",
        "--output-format", "text",
        "--no-session-persistence",
        "--permission-mode", "dontAsk",
        "--tools", "",
        "--system-prompt", SYSTEM_PROMPT,
    ]
    if model:
        args += ["--model", model]

    try:
        result = subprocess.run(
            args,
            input=prompt,
            cwd=root_dir(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError as error:
        raise RuntimeError(
            "Claude CLI not found. Install it and run `claude auth`, or set "
            "MP_CLAUDE_CLI_COMMAND."
        ) from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"Claude did not answer within {timeout}s") from error

    text = (result.stdout or "").strip()
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"claude --print exited {result.returncode}: {detail[-800:]}")
    if not text:
        raise RuntimeError("Claude returned an empty answer")
    return text


# -- reading the answer ------------------------------------------------------


def parse_response(text: str) -> dict[str, Any]:
    """Extract the JSON object from an answer. {} when nothing parses."""
    if not text:
        return {}
    candidate = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", candidate, re.DOTALL)
    if fenced:
        candidate = fenced.group(1)
    else:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start == -1 or end <= start:
            return {}
        candidate = candidate[start:end + 1]
    try:
        parsed = json.loads(candidate)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _slug(value: Any, fallback: str) -> str:
    text = re.sub(r"[^a-z0-9_]+", "_", str(value or "").strip().lower()).strip("_")
    return text[:48] or fallback


def extract_proposals(
    parsed: dict[str, Any],
    max_experiments: int,
    tried: set[str] | None = None,
) -> dict[str, Any]:
    """Validate the advisor's experiments. Nothing unsafe gets through."""
    tried = set(tried if tried is not None else ledger.tried_change_sets())
    approved, _ = knobs.approved_overrides()
    current = ledger.canonical(approved)

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen: set[str] = set()

    raw_experiments = parsed.get("experiments")
    if not isinstance(raw_experiments, list):
        raw_experiments = []

    for position, item in enumerate(raw_experiments, start=1):
        if not isinstance(item, dict):
            rejected.append({"item": item, "reason": "not an object"})
            continue
        name = _slug(item.get("name"), f"experiment_{position}")
        clean, errors = knobs.validate(item.get("changes"))
        if errors:
            rejected.append({"name": name, "changes": item.get("changes"),
                             "reason": "; ".join(errors)})
            continue
        if not clean:
            rejected.append({"name": name, "reason": "no changes"})
            continue

        identity = ledger.canonical({**approved, **clean})
        if identity == current:
            rejected.append({"name": name, "changes": clean,
                             "reason": "identical to the current configuration"})
            continue
        if ledger.canonical(clean) in tried or identity in tried:
            rejected.append({"name": name, "changes": clean,
                             "reason": "already tried in an earlier round"})
            continue
        if identity in seen:
            rejected.append({"name": name, "changes": clean,
                             "reason": "duplicate within this answer"})
            continue
        if len(accepted) >= max_experiments:
            rejected.append({"name": name, "changes": clean,
                             "reason": f"over the limit of {max_experiments} per round"})
            continue

        seen.add(identity)
        accepted.append({
            "name": name,
            "hypothesis": str(item.get("hypothesis") or "")[:600],
            "changes": clean,
        })

    ideas = parsed.get("feature_ideas")
    priorities = parsed.get("data_priorities")
    return {
        "diagnosis": str(parsed.get("diagnosis") or "")[:2000],
        "experiments": accepted,
        "rejected": rejected,
        "feature_ideas": [
            {
                "name": _slug(idea.get("name"), "idea"),
                "rationale": str(idea.get("rationale") or "")[:600],
                "data_needed": str(idea.get("data_needed") or "")[:300],
            }
            for idea in (ideas if isinstance(ideas, list) else [])
            if isinstance(idea, dict)
        ][:8],
        "data_priorities": [
            str(item)[:300] for item in (priorities if isinstance(priorities, list) else [])
        ][:6],
    }
