"""Deciding which candidate is worth cutting, and what to call it.

Two layers, and the split matters:

  - **Heuristics** (pure, no network) judge the shape of a span: does it
    fit the length that wins, does it open on a hook instead of
    mid-thought, does it finish its sentence, is anyone actually talking.
    These run always and are what the tests cover.

  - **An LLM pass** judges the thing heuristics cannot see — whether the
    span makes a point that lands without the hour of video around it —
    and writes the title. It is given the trend brief from `trends.py`, so
    the title it writes is shaped by what measurably wins on comparable
    channels rather than by the model's taste.

If the LLM is unreachable the module still produces clips, ranked by
heuristics alone, and says so. That is the difference between a degraded
run and a failed one.
"""

from __future__ import annotations

import json
import re
from typing import Any, Sequence

from .segments import Window


#: How much of the final score each layer carries when both are present.
LLM_WEIGHT = 0.65
HEURISTIC_WEIGHT = 0.35

#: Most candidates the model is shown. The heuristics already rank them, so
#: the tail is filler; sending it only makes the prompt long enough for
#: smaller models to lose the output format.
LLM_MAX_CANDIDATES = 20

#: Speech rate sanity band, words per second. Below the floor the span is
#: mostly silence or music; above the ceiling the transcript is probably
#: wrong.
MIN_WORDS_PER_SECOND = 1.4
IDEAL_WORDS_PER_SECOND = 2.6

#: Openers that mean the span starts in the middle of an argument. A clip
#: that begins on "but" asks the viewer to remember something they never
#: saw.
_DANGLING_OPENER = re.compile(
    r"^\s*(y|e|o|u|pero|mas|sino|entonces|porque|pues|as[ií]|adem[aá]s|tambi[eé]n"
    r"|luego|despu[eé]s|por eso|es decir|o sea|and|but|so|because|then|also"
    r"|however|therefore|which|that)\b",
    re.IGNORECASE,
)

#: A span that opens on one of these is already a hook.
_STRONG_OPENER = re.compile(
    r"^\s*([¿¿]|qu[eé]\b|c[oó]mo\b|cu[aá]ndo\b|cu[aá]nto\b|por qu[eé]\b|d[oó]nde\b"
    r"|qui[eé]n\b|imagina\b|nadie\b|nunca\b|esto\b|aqu[ií]\b|mira\b|escucha\b"
    r"|what\b|how\b|why\b|when\b|who\b|imagine\b|nobody\b|never\b|this\b|look\b"
    r"|listen\b|\d)",
    re.IGNORECASE,
)

_SENTENCE_FINAL = re.compile(r"[.!?…][\"'”»)\]]*\s*$")


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def length_fit(duration: float, low: float, high: float) -> float:
    """1.0 at the centre of the target band, tapering to 0 outside it.

    Not a step function: a clip two seconds over the band is barely worse
    than one inside it, and a hard cutoff would throw away good content
    for an arbitrary boundary.
    """
    if high <= low:
        return 1.0 if abs(duration - low) < 1.0 else 0.0
    centre = (low + high) / 2.0
    half = (high - low) / 2.0
    # Allow a 30% grace margin beyond the band before the score hits zero.
    return _clamp(1.0 - (abs(duration - centre) / (half * 1.3)))


def speech_density(word_count: int, duration: float) -> float:
    """How much is actually said per second, as a 0-1 score."""
    if duration <= 0:
        return 0.0
    rate = word_count / duration
    if rate < MIN_WORDS_PER_SECOND:
        return _clamp(rate / MIN_WORDS_PER_SECOND) * 0.6
    if rate <= IDEAL_WORDS_PER_SECOND:
        return 1.0
    return _clamp(1.0 - (rate - IDEAL_WORDS_PER_SECOND) / IDEAL_WORDS_PER_SECOND)


def heuristic_score(window: Window, low: float, high: float) -> dict[str, float]:
    """The pure, always-available judgement of one candidate span."""
    text = (window.text or "").strip()
    word_count = len([w for w in re.split(r"\s+", text) if w])

    parts = {
        "length_fit": length_fit(window.duration, low, high),
        "density": speech_density(word_count, window.duration),
        "opens_clean": 0.0 if _DANGLING_OPENER.match(text) else 1.0,
        "hooky_open": 1.0 if _STRONG_OPENER.match(text) else 0.0,
        "finishes": 1.0 if _SENTENCE_FINAL.search(text) else 0.35,
    }
    weights = {
        "length_fit": 0.30,
        "density": 0.20,
        "opens_clean": 0.25,
        "hooky_open": 0.10,
        "finishes": 0.15,
    }
    parts["total"] = round(sum(parts[k] * w for k, w in weights.items()), 4)
    return parts


# -- The LLM pass ------------------------------------------------------


def _parse_json_from_llm(text: str) -> Any:
    """JSON out of a model response that may be fenced or chatty."""
    text = (text or "").strip()
    for attempt in (text, re.sub(r"^```(?:json)?\s*", "", text).rstrip("`").strip()):
        try:
            return json.loads(attempt)
        except (json.JSONDecodeError, TypeError):
            continue
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass
    for pattern in (r"(\[[\s\S]*\])", r"(\{[\s\S]*\})"):
        match = re.search(pattern, text)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
    # Last resort: a reply cut off mid-array. Free and small models stop at
    # their output limit without closing the JSON, but every object before
    # the cut is complete and usable. The objects asked for are flat, so a
    # brace-free match is enough to recover them one by one.
    recovered = []
    for match in re.finditer(r"\{[^{}]*\}", text):
        try:
            item = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            recovered.append(item)
    return recovered or None


def build_prompt(
    windows: Sequence[Window],
    trend_brief: str,
    source_title: str = "",
    language: str = "",
) -> str:
    """One prompt covering every candidate, asking for one JSON array."""
    listing = []
    for index, window in enumerate(windows):
        body = window.text if len(window.text) <= 900 else window.text[:900] + "..."
        listing.append(
            f"[{index}] ({window.duration:.0f}s) {body}"
        )
    language_line = (
        f"Write every title in {language}."
        if language
        else "Write each title in the same language as the transcript."
    )
    return f"""You pick vertical short-form clips out of a long video, and you are
judged on whether a stranger scrolling past would stop and watch to the end.

Source video: {source_title or "(untitled)"}

What measurably wins on comparable channels:
{trend_brief}

Below are candidate excerpts, each already cut at sentence boundaries.
Score every one of them. For each, judge:

- "hook": would the first two seconds stop a scroll? (0-10)
- "payoff": does it deliver something — a fact, a reveal, a punchline —
  rather than just set up? (0-10)
- "standalone": does it make sense with no other context from the video?
  An excerpt that refers to "that" or "as I said" is not standalone. (0-10)
- "title": a title for this clip, following the winning patterns above.
  {language_line}

Be harsh. Most excerpts in any video are filler, and a 4 is a normal score.

CANDIDATES:
{chr(10).join(listing)}

Reply with ONLY a JSON array, one object per candidate, nothing else:
[{{"id": 0, "hook": 7, "payoff": 5, "standalone": 8, "title": "..."}}]
"""


def apply_llm_scores(windows: Sequence[Window], parsed: Any) -> int:
    """Fold a parsed model reply into the windows. Returns how many landed.

    Separate from the call itself so it can be tested against a malformed
    reply without a model: a model that renumbers, repeats or invents an
    index must not corrupt another candidate's score.
    """
    if isinstance(parsed, dict):
        parsed = parsed.get("clips") or parsed.get("candidates") or []
    if not isinstance(parsed, list):
        return 0

    applied = 0
    seen: set[int] = set()
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        try:
            index = int(entry.get("id", entry.get("index", -1)))
        except (TypeError, ValueError):
            continue
        if not (0 <= index < len(windows)) or index in seen:
            continue
        seen.add(index)

        def number(key: str) -> float:
            try:
                return _clamp(float(entry.get(key, 0)) / 10.0)
            except (TypeError, ValueError):
                return 0.0

        hook, payoff, standalone = number("hook"), number("payoff"), number("standalone")
        window = windows[index]
        window.scores.update(
            {
                "hook": round(hook, 3),
                "payoff": round(payoff, 3),
                "standalone": round(standalone, 3),
                # A clip that needs the rest of the video is worthless on its
                # own, so standalone carries the most weight here.
                "llm": round(hook * 0.35 + payoff * 0.25 + standalone * 0.40, 4),
            }
        )
        title = str(entry.get("title") or "").strip().strip('"')
        if title:
            window.title = title[:120]
        applied += 1
    return applied


def score_windows(
    windows: Sequence[Window],
    low: float,
    high: float,
    trend_brief: str = "",
    source_title: str = "",
    language: str = "",
    use_llm: bool = True,
    on_status=None,
) -> dict[str, Any]:
    """Score every candidate, blending heuristics with the LLM pass.

    Always fills `window.score`. Returns a short report saying which
    layers actually ran, so the caller can tell the user that a run was
    heuristics-only instead of silently shipping worse clips.
    """
    for window in windows:
        parts = heuristic_score(window, low, high)
        window.scores.update(parts)
        window.score = parts["total"]

    report: dict[str, Any] = {
        "candidates": len(windows),
        "heuristics": True,
        "llm": False,
        "llm_scored": 0,
        "llm_error": "",
    }
    if not use_llm or not windows:
        return report

    try:
        from llm_provider import generate_text  # imported late: optional dependency
    except Exception as exc:  # pragma: no cover - environment dependent
        report["llm_error"] = f"llm_provider unavailable ({exc})"
        return report

    shortlist = sorted(windows, key=lambda w: -w.score)[:LLM_MAX_CANDIDATES]
    if on_status:
        on_status(f"scoring the top {len(shortlist)} candidates with the model")
    reply = ""
    try:
        reply = generate_text(
            build_prompt(shortlist, trend_brief, source_title, language),
            temperature=0.4,
        )
        applied = apply_llm_scores(shortlist, _parse_json_from_llm(reply))
    except Exception as exc:
        report["llm_error"] = str(exc)[:300]
        return report

    if not applied:
        snippet = re.sub(r"\s+", " ", str(reply or "")).strip()[:160]
        report["llm_error"] = (
            "the model's reply could not be read as scores"
            + (f" (it began: {snippet!r})" if snippet else " (empty reply)")
        )
        return report

    for window in windows:
        llm = window.scores.get("llm")
        if llm is None:
            # Not scored by the model: keep the heuristic score but hold it
            # below anything the model actually looked at, rather than
            # letting an unjudged span win by default.
            window.score = round(window.scores["total"] * HEURISTIC_WEIGHT, 4)
            continue
        window.score = round(
            float(llm) * LLM_WEIGHT + window.scores["total"] * HEURISTIC_WEIGHT, 4
        )
    report.update({"llm": True, "llm_scored": applied})
    return report


def fallback_title(window: Window, source_title: str = "") -> str:
    """A title when the model gave none: the clip's own opening words."""
    if window.title:
        return window.title
    text = re.sub(r"\s+", " ", (window.text or "").strip())
    if not text:
        return (source_title or "Clip")[:80]
    words = text.split()
    return " ".join(words[:9]).rstrip(".,;:") or (source_title or "Clip")[:80]
