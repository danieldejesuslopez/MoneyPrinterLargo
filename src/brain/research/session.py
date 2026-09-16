"""A long research conversation between Brain and the user's Claude account.

    brain research --session 60

A single research round asks once and tests a handful of knob changes. A
session is a conversation: Claude reads Brain's dossier, asks for one
analysis, reads the result, forms or drops a hypothesis, asks for the next,
and keeps going until the clock runs out or it decides more evidence would
not change its mind. Then it writes a report.

The conversation uses a real Claude Code session on the user's account -
`--session-id` on the first turn, `--resume` after that - so Claude keeps its
own memory of the investigation and Brain only has to send each new result.

What does not change, however long it runs:

  - Claude can only name analyses from `analyses.MENU`. It cannot run code,
    read files or edit configuration; every tool is disabled on each call.
  - Experiments spend the same confirmation budget as `brain research`, and
    are written to the ledger the moment they finish.
  - Nothing is adopted. The report's recommendations are filtered down to
    experiments that actually passed both eras during the session; anything
    else Claude claims is kept, labelled, and not offered as a change.

Files, written as the session runs so it can be followed live:

    .mp/brain/sessions/<id>/transcript.md   human-readable conversation
    .mp/brain/sessions/<id>/turns.jsonl     every turn, machine-readable
    .mp/brain/sessions/<id>/report.md       final report (and report.json)
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import subprocess
import time
import uuid
from typing import Any, Callable

from ..config import cache_dir, root_dir
from . import advisor, analyses, dossier, ledger, loop

AskTurn = Callable[[str, bool], str]
Progress = Callable[[str], None]

DEFAULT_MINUTES = 60
DEFAULT_MAX_TURNS = 60
#: Kept free at the end so the closing report always has time to be written.
SYNTHESIS_RESERVE_SECONDS = 180
#: Floor for how long an experiment is assumed to take. The real estimate is
#: measured at the start of each session (an experiment reruns the same
#: walk-forward, plus a dataset rebuild for label knobs); this only stops a
#: suspiciously fast measurement from licensing an experiment in the last
#: seconds.
EXPERIMENT_MIN_SECONDS = 90
#: Multiple of the measured walk-forward time budgeted per experiment.
EXPERIMENT_TIME_FACTOR = 3.0
#: If Claude declares itself done with more than this share of the session
#: still left, it is asked - once - to spend the time challenging its own
#: conclusions. A second "done" is final: forcing turns past that point
#: produces churn, not evidence.
EARLY_DONE_SHARE = 0.5
MAX_CONSECUTIVE_FAILURES = 3
CALL_TIMEOUT_SECONDS = 600
RESULT_CHAR_LIMIT = 6000


def sessions_dir() -> str:
    path = os.path.join(cache_dir(), "sessions")
    os.makedirs(path, exist_ok=True)
    return path


# -- talking to Claude ---------------------------------------------------------


def cli_session(session_id: str, model: str = "") -> AskTurn:
    """A callable that continues one Claude Code session across calls."""
    cli = advisor.resolve_cli()
    settings = advisor.research_settings()
    model = model or settings["model"]

    def ask(prompt: str, first: bool) -> str:
        args = [
            cli, "--print",
            "--output-format", "text",
            "--permission-mode", "dontAsk",
            "--tools", "",
            "--system-prompt", advisor.SYSTEM_PROMPT,
        ]
        args += ["--session-id", session_id] if first else ["--resume", session_id]
        if model:
            args += ["--model", model]
        result = subprocess.run(
            args, input=prompt, cwd=root_dir(), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=CALL_TIMEOUT_SECONDS,
        )
        text = (result.stdout or "").strip()
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"claude exited {result.returncode}: {detail[-600:]}")
        if not text:
            raise RuntimeError("Claude returned an empty answer")
        return text

    return ask


# -- prompts ---------------------------------------------------------------------


REPLY_SHAPE = """{
  "reading": "what the last result tells you",
  "findings": ["cumulative conclusions so far, each citing its evidence"],
  "next": {"analysis": "<name from the menu>", "params": {},
           "why": "what this could confirm or rule out"},
  "done": false
}"""


GOALS: dict[str, str] = {
    "model": """Find out why the model cannot beat the same-format historical mean
(format_gain_pct is negative in every era) and what would change that. The
global null is already beaten; that is not the question.""",
    "strategy": """The model question is settled for now: inside a format the signal is weak
and mostly noise at this sample size. This phase is about what the CREATOR
should do, not the model:
1. Republishing. Several topics were published more than once. Does a repeat
   cannibalise the original or ride its momentum? What should the rule be?
2. Content guidance. Which properties of the best Shorts - thumbnail, title,
   format, timing - survive compare_groups with an interval that excludes
   zero, and which are noise?
3. Learning plan. How should the next 20-40 videos be deliberately varied so
   the channel learns fastest what works?
Advice that compare_groups has not separated from zero must be framed as a
test to run on future videos, with how many videos it needs - never as a
rule.""",
}


def opening_prompt(
    brief: dict[str, Any], minutes: int, max_turns: int, budget: int, goal: str = "model"
) -> str:
    return f"""MoneyPrinter Brain has opened a live research session with you. It lasts
about {minutes} minutes and at most {max_turns} turns.

GOAL
{GOALS[goal]}

HOW THIS WORKS
- Each turn, ask Brain for ONE analysis from the menu below. Brain computes it
  on the real channel data and sends the result as your next message.
- You cannot run code, read files or change configuration. Nothing you say is
  adopted automatically; a person reads the final report.
- "experiment" retrains the model, takes several minutes, and spends the
  shared confirmation budget ({budget} left). Use it only to test a hypothesis
  your analyses already support - never to explore.
- The sample is small (about 73 Shorts). Correlations below a stated noise
  threshold are noise. Prefer analyses that could DISPROVE your current idea.
- Do not request the same analysis with the same parameters twice.

REPLY EVERY TURN WITH EXACTLY THIS JSON, text in Spanish:
{REPLY_SHAPE}
When more analysis would not change your conclusions, set "done": true and
omit "next".

ANALYSIS MENU
{json.dumps(analyses.describe_menu(), ensure_ascii=False, indent=1)}

DOSSIER
{json.dumps(brief, ensure_ascii=False, indent=1, default=str)}
"""


def continuation_prompt(goal: str, minutes: int, max_turns: int, budget: int) -> str:
    """A new phase in the same Claude session: it already has the dossier."""
    return f"""A NEW PHASE of this research session begins now. You keep everything you
learned in the previous phase; do not repeat its analyses. It lasts about
{minutes} minutes and at most {max_turns} turns, and {budget} experiments are
left in the shared budget.

NEW GOAL
{GOALS[goal]}

The same rules apply: one analysis per turn from the menu, nothing you say is
adopted automatically, correlations below the noise threshold are noise, and
prefer analyses that could disprove your idea. The menu has new entries.

REPLY EVERY TURN WITH EXACTLY THIS JSON, text in Spanish:
{REPLY_SHAPE}

ANALYSIS MENU
{json.dumps(analyses.describe_menu(), ensure_ascii=False, indent=1)}
"""


def result_prompt(name: str, params: Any, result: dict[str, Any], status: str) -> str:
    body = json.dumps(result, ensure_ascii=False, default=str)
    if len(body) > RESULT_CHAR_LIMIT:
        body = body[:RESULT_CHAR_LIMIT] + " ...[truncated]"
    return (
        f"RESULT of {name} {json.dumps(params, ensure_ascii=False)}\n{body}\n\n"
        f"{status}\nReply with the same JSON shape."
    )


def error_prompt(message: str, status: str) -> str:
    return (
        f"Brain could not do that: {message}\n\n{status}\n"
        "Reply with the same JSON shape."
    )


SYNTHESIS_SHAPE = """{
  "summary": "3 to 5 sentences",
  "key_findings": [{"finding": "...", "evidence": "which analysis showed it",
                    "confidence": "alta|media|baja"}],
  "recommended_changes": [{"changes": {"<knob>": "value"}, "evidence": "..."}],
  "hypotheses_for_future_videos": [{"hypothesis": "...", "how_to_check": "...",
                                    "videos_needed": 20}],
  "feature_ideas": [{"name": "slug", "rationale": "...", "data_needed": "..."}],
  "data_priorities": ["..."],
  "what_not_to_do": ["..."]
}"""


STRATEGY_EXTRA = """
Also include, because this phase was about the creator:
  "republishing_rule": {"rule": "...", "evidence": "...", "confidence": "alta|media|baja"},
  "content_guidelines": [{"guideline": "...", "evidence": "...",
                          "proven": true_only_if_compare_groups_excluded_zero,
                          "confidence": "alta|media|baja"}],
  "learning_plan": [{"step": 1, "videos": 10, "what_to_vary": "...",
                     "what_to_measure": "...", "decision_rule": "..."}]"""


def synthesis_prompt(experiments: list[dict[str, Any]], goal: str = "model") -> str:
    passed = [e for e in experiments
              if (e.get("comparison") or {}).get("verdict") == "recommended"]
    return f"""The session is over. Write the final report as ONE JSON object, text in
Spanish. Claim only what the analyses in this session actually showed, and say
how confident you are in each finding.

"recommended_changes" may ONLY contain experiments that passed both eras in
this session. Passed this session: {json.dumps([e.get("changes") for e in passed])}.
If that list is empty, "recommended_changes" must be [].

{SYNTHESIS_SHAPE}{STRATEGY_EXTRA if goal == "strategy" else ""}"""


# -- the conversation ------------------------------------------------------------


class Transcript:
    def __init__(self, directory: str):
        self.directory = directory
        self.md_path = os.path.join(directory, "transcript.md")
        self.jsonl_path = os.path.join(directory, "turns.jsonl")

    def write(self, text: str) -> None:
        with open(self.md_path, "a", encoding="utf-8") as file:
            file.write(text + "\n")

    def record(self, entry: dict[str, Any]) -> None:
        with open(self.jsonl_path, "a", encoding="utf-8") as file:
            file.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")


def _status(deadline: float, clock: Callable[[], float], turns_left: int, budget: int) -> str:
    minutes = max(0.0, (deadline - clock()) / 60.0)
    return (f"{minutes:.0f} minutes left, {turns_left} turns left, "
            f"{budget} experiments left in the budget.")


def _as_markdown_list(items: Any) -> str:
    if not isinstance(items, list) or not items:
        return "_(nada)_"
    return "\n".join(f"- {item}" for item in items)


def run_session(
    minutes: int = DEFAULT_MINUTES,
    max_turns: int = DEFAULT_MAX_TURNS,
    ask_turn: AskTurn | None = None,
    clock: Callable[[], float] = time.monotonic,
    progress: Progress = print,
    stride: int = 1,
    goal: str = "model",
    continue_from: str | None = None,
) -> dict[str, Any]:
    if goal not in GOALS:
        raise ValueError(f"goal must be one of {sorted(GOALS)}")

    started = clock()
    deadline = started + minutes * 60
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d-%H%M%S")

    # Continuing resumes the SAME Claude session, so everything the previous
    # phase established stays in Claude's memory instead of being re-briefed.
    previous = load_session(continue_from) if continue_from else None
    session_id = previous["claude_session_id"] if previous else str(uuid.uuid4())

    directory = os.path.join(sessions_dir(), stamp)
    os.makedirs(directory, exist_ok=True)
    transcript = Transcript(directory)
    ask = ask_turn or cli_session(session_id)

    transcript.write(
        f"# Sesion de investigacion {stamp}\n\n"
        f"- objetivo: {goal}, ~{minutes} min, hasta {max_turns} turnos\n"
        f"- sesion de Claude: `{session_id}`"
        + (f" (continua la sesion {continue_from})" if previous else "")
        + f"\n- inicio: {_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    )
    progress(f"  sesion {stamp} -> {transcript.md_path}")

    progress("  midiendo el modelo actual (una sola vez para toda la sesion)...")
    measured_from = clock()
    base_rows, current = loop.prepare(stride=stride, progress=lambda m: None)
    experiment_seconds = max(
        EXPERIMENT_MIN_SECONDS, EXPERIMENT_TIME_FACTOR * (clock() - measured_from)
    )
    ctx = analyses.Context(base_rows, current)
    _, budget = analyses.experiment_budget(current)

    if previous:
        prompt = continuation_prompt(goal, minutes, max_turns, budget)
    else:
        brief = dossier.build_dossier(base_rows, current, "format", budget)
        prompt = opening_prompt(brief, minutes, max_turns, budget, goal)

    turns: list[dict[str, Any]] = []
    experiments: list[dict[str, Any]] = []
    findings: list[Any] = []
    requested: set[str] = set()
    # Two separate counters: a successful call must not forgive a run of
    # unreadable replies, or three garbage answers in a row never end it.
    call_failures = 0
    invalid_replies = 0
    nudged = False
    # A continued session already exists on Claude's side: resume from turn one.
    first = previous is None
    spoke = False
    ended_by = "turns"

    for turn in range(1, max_turns + 1):
        if deadline - clock() <= SYNTHESIS_RESERVE_SECONDS:
            ended_by = "time"
            break

        progress(f"  turno {turn}: esperando a Claude...")
        try:
            raw = ask(prompt, first)
            first = False
            spoke = True
            call_failures = 0
        except Exception as error:
            call_failures += 1
            transcript.write(f"\n> fallo al hablar con Claude: {error}\n")
            if call_failures >= MAX_CONSECUTIVE_FAILURES:
                ended_by = "failures"
                break
            time.sleep(2)
            continue

        reply = advisor.parse_response(raw)
        entry: dict[str, Any] = {"turn": turn, "elapsed_s": round(clock() - started)}

        if not reply:
            invalid_replies += 1
            entry["error"] = "reply was not valid JSON"
            transcript.write(f"\n## Turno {turn}\n\n_respuesta no valida (no era JSON)_\n")
            transcript.record(entry)
            if invalid_replies >= MAX_CONSECUTIVE_FAILURES:
                ended_by = "failures"
                break
            prompt = error_prompt(
                "your reply was not a JSON object with reading, findings, next, done",
                _status(deadline, clock, max_turns - turn, budget),
            )
            continue

        invalid_replies = 0
        if isinstance(reply.get("findings"), list) and reply["findings"]:
            findings = reply["findings"]
        entry.update({"reading": reply.get("reading"), "findings": reply.get("findings"),
                      "next": reply.get("next"), "done": bool(reply.get("done"))})

        transcript.write(
            f"\n## Turno {turn} - Claude  _(min {entry['elapsed_s'] // 60})_\n\n"
            f"**Lectura:** {reply.get('reading') or '-'}\n\n"
            f"**Hallazgos hasta ahora:**\n{_as_markdown_list(reply.get('findings'))}\n"
        )

        if reply.get("done"):
            share_left = (deadline - clock()) / max(1.0, deadline - started)
            if not nudged and share_left > EARLY_DONE_SHARE and turn < max_turns:
                nudged = True
                transcript.write(
                    "\n_Claude quiere cerrar con mas de la mitad del tiempo por "
                    "delante; Brain le pide usarlo para poner a prueba sus hallazgos._\n"
                )
                transcript.record(entry)
                turns.append(entry)
                prompt = error_prompt(
                    "more than half of the session is still left. Before closing, use "
                    "it to try to break your own strongest finding: look for an "
                    "alternative explanation, or check whether it survives a different "
                    "cut of the data. If you are still confident afterwards, say done",
                    _status(deadline, clock, max_turns - turn, budget),
                )
                continue
            transcript.write("\n_Claude da la investigacion por cerrada._\n")
            transcript.record(entry)
            turns.append(entry)
            ended_by = "claude"
            break

        step = reply.get("next") if isinstance(reply.get("next"), dict) else {}
        name = str(step.get("analysis") or "")
        params = step.get("params") if isinstance(step.get("params"), dict) else {}
        transcript.write(
            f"\n**Pide:** `{name}` `{json.dumps(params, ensure_ascii=False)}`  \n"
            f"**Por que:** {step.get('why') or '-'}\n"
        )
        status_turns = max_turns - turn

        key = f"{name}:{json.dumps(params, sort_keys=True, ensure_ascii=False)}"
        try:
            if key in requested:
                raise analyses.AnalysisError("you already requested exactly this")
            analysis = analyses.MENU_BY_NAME.get(name)
            if analysis and analysis.expensive and deadline - clock() < (
                experiment_seconds + SYNTHESIS_RESERVE_SECONDS
            ):
                raise analyses.AnalysisError(
                    "not enough time left to run an experiment; choose a cheap analysis"
                )
            requested.add(key)
            if analysis and analysis.expensive:
                progress(f"  turno {turn}: experimento {params.get('changes')} (minutos)...")
            result = analyses.run(ctx, name, params)
            if name == "experiment":
                experiments.append(result)
                _, budget = analyses.experiment_budget(current)
            entry["result"] = result
            transcript.write(
                f"\n<details><summary>Brain calcula {name}</summary>\n\n```json\n"
                f"{json.dumps(result, ensure_ascii=False, indent=1, default=str)[:4000]}\n"
                "```\n</details>\n"
            )
            prompt = result_prompt(
                name, params, result, _status(deadline, clock, status_turns, budget)
            )
        except analyses.AnalysisError as error:
            entry["analysis_error"] = str(error)
            transcript.write(f"\n> Brain no puede: {error}\n")
            prompt = error_prompt(str(error), _status(deadline, clock, status_turns, budget))
        except Exception as error:  # an analysis bug must not end the session
            entry["analysis_error"] = f"internal error: {error}"
            transcript.write(f"\n> error interno en {name}: {error}\n")
            prompt = error_prompt(
                f"internal error while computing {name}; choose a different analysis",
                _status(deadline, clock, status_turns, budget),
            )

        transcript.record(entry)
        turns.append(entry)

    report = _close(ask, spoke, experiments, findings, transcript, progress, goal)
    summary = {
        "session": stamp,
        "goal": goal,
        "continued_from": continue_from if previous else None,
        "claude_session_id": session_id,
        "directory": directory,
        "minutes_target": minutes,
        "minutes_used": round((clock() - started) / 60.0, 1),
        "turns": len(turns),
        "ended_by": ended_by,
        "experiments": [
            {"changes": e.get("changes"),
             "verdict": (e.get("comparison") or {}).get("verdict")}
            for e in experiments
        ],
        "report": report,
    }
    with open(os.path.join(directory, "report.json"), "w", encoding="utf-8") as file:
        json.dump(summary, file, ensure_ascii=False, indent=2, default=str)
    _write_report_md(directory, summary)
    ledger.record_round({
        "round_id": f"session-{stamp}",
        "created_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "source": "session_summary",
        "summary": {k: v for k, v in summary.items() if k != "report"},
        "feature_ideas": (report or {}).get("feature_ideas", []),
        "data_priorities": (report or {}).get("data_priorities", []),
    })
    return summary


def load_session(stamp: str) -> dict[str, Any]:
    """The saved summary of an earlier session, to continue its conversation."""
    path = os.path.join(sessions_dir(), str(stamp), "report.json")
    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, ValueError) as error:
        raise FileNotFoundError(f"no finished session {stamp!r} ({path})") from error
    if not data.get("claude_session_id"):
        raise ValueError(f"session {stamp!r} has no Claude session id to resume")
    return data


def _close(
    ask: AskTurn,
    spoke: bool,
    experiments: list[dict[str, Any]],
    findings: list[Any],
    transcript: Transcript,
    progress: Progress,
    goal: str = "model",
) -> dict[str, Any]:
    """Ask for the final report and strip anything it cannot back up."""
    if not spoke:
        return {"summary": "La sesion no llego a hablar con Claude.",
                "key_findings": [], "recommended_changes": []}

    progress("  pidiendo el informe final...")
    try:
        report = advisor.parse_response(ask(synthesis_prompt(experiments, goal), False))
    except Exception as error:
        transcript.write(f"\n> no se pudo obtener el informe final: {error}\n")
        report = {}
    if not report:
        report = {"summary": "Claude no entrego un informe final valido; "
                             "se conservan los hallazgos del ultimo turno.",
                  "key_findings": [{"finding": str(f), "evidence": "ultimo turno",
                                    "confidence": "?"} for f in findings]}

    passed = {
        ledger.canonical(e.get("changes") or {})
        for e in experiments
        if (e.get("comparison") or {}).get("verdict") == "recommended"
    }
    kept, dropped = [], []
    for item in report.get("recommended_changes") or []:
        changes = item.get("changes") if isinstance(item, dict) else None
        if isinstance(changes, dict) and ledger.canonical(changes) in passed:
            kept.append(item)
        else:
            dropped.append(item)
    report["recommended_changes"] = kept
    if dropped:
        report["unsupported_recommendations"] = dropped

    transcript.write("\n---\n\n# Informe final\n")
    transcript.write(f"\n{report.get('summary') or ''}\n")
    return report


def _write_report_md(directory: str, summary: dict[str, Any]) -> None:
    report = summary.get("report") or {}
    lines = [
        f"# Informe de la sesion {summary['session']}",
        "",
        f"{summary['minutes_used']} min · {summary['turns']} turnos · "
        f"terminada por: {summary['ended_by']}",
        "",
        "## Resumen",
        "",
        str(report.get("summary") or "-"),
        "",
        "## Hallazgos",
        "",
    ]
    for item in report.get("key_findings") or []:
        if isinstance(item, dict):
            lines.append(f"- **[{item.get('confidence', '?')}]** {item.get('finding')}  ")
            lines.append(f"  _evidencia: {item.get('evidence')}_")
        else:
            lines.append(f"- {item}")
    lines += ["", "## Experimentos de la sesion", ""]
    for exp in summary.get("experiments") or []:
        lines.append(f"- `{exp['changes']}` -> {exp['verdict']}")
    if not summary.get("experiments"):
        lines.append("_(ninguno)_")
    lines += ["", "## Cambios recomendados (pasaron ambas eras)", ""]
    for item in report.get("recommended_changes") or []:
        lines.append(f"- `{item.get('changes')}`: {item.get('evidence')}")
    if not report.get("recommended_changes"):
        lines.append("_(ninguno)_")
    if report.get("unsupported_recommendations"):
        lines += ["", "## Recomendaciones de Claude SIN respaldo experimental (no aplicar)", ""]
        for item in report["unsupported_recommendations"]:
            lines.append(f"- `{item}`")
    rule = report.get("republishing_rule")
    if isinstance(rule, dict) and rule:
        lines += ["", "## Regla de republicacion", "",
                  f"**[{rule.get('confidence', '?')}]** {rule.get('rule')}  ",
                  f"_evidencia: {rule.get('evidence')}_"]
    guidelines = report.get("content_guidelines") or []
    if guidelines:
        lines += ["", "## Pautas de contenido", ""]
        for item in guidelines:
            if not isinstance(item, dict):
                continue
            tag = "PROBADA" if item.get("proven") else "hipotesis a probar"
            lines.append(f"- **[{tag} · {item.get('confidence', '?')}]** {item.get('guideline')}  ")
            lines.append(f"  _evidencia: {item.get('evidence')}_")
    plan = report.get("learning_plan") or []
    if plan:
        lines += ["", "## Plan de aprendizaje", ""]
        for item in plan:
            if isinstance(item, dict):
                lines.append(
                    f"{item.get('step', '-')}. **{item.get('videos', '?')} videos** - "
                    f"variar: {item.get('what_to_vary')} · medir: {item.get('what_to_measure')} · "
                    f"decidir: {item.get('decision_rule')}"
                )
    for title, key in (("Hipotesis para videos futuros", "hypotheses_for_future_videos"),
                       ("Ideas de features", "feature_ideas"),
                       ("Prioridades de datos", "data_priorities"),
                       ("Que NO hacer", "what_not_to_do")):
        lines += ["", f"## {title}", ""]
        items = report.get(key) or []
        for item in items:
            lines.append(f"- {json.dumps(item, ensure_ascii=False) if isinstance(item, dict) else item}")
        if not items:
            lines.append("_(nada)_")
    with open(os.path.join(directory, "report.md"), "w", encoding="utf-8") as file:
        file.write("\n".join(lines) + "\n")
