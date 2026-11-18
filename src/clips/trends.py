"""What actually wins in short form, learned from real channel history.

This is the "learns from trending videos" half of the module. It does not
scrape a trending feed — a feed tells you what is popular *today* without
telling you why, and it is not reliably listable. What it reads instead is
history this installation already has:

  - `.mp/brain/reference_channels.json` — the full history of the
    reference channels Brain imported, winners next to losers.
  - `.mp/youtube.json` — this installation's own channels.

Only shorts are considered, and a video's success is its view count over
*its own channel's* median, so a 100k channel and a 1k channel contribute
on the same scale (the `normalized_views` idea from `src/brain/`).

The honest part is the comparison. A trait shared equally by winners and
losers explains nothing, so every title trait is reported as a **lift**:
how much more common it is among the top decile than among the bottom
half. Lift near 1.0 means "this trait is noise", and the profile says so
rather than dressing it up as a finding.

Stdlib only, no network. `profile()` reads two JSON files and returns a
dict; nothing here renders or downloads anything.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Iterable

from . import config as clips_config


#: Below this many shorts on a channel, its median is not a median and the
#: channel is skipped rather than contributing noise.
MIN_SHORTS_PER_CHANNEL = 12

#: Below this many exemplars in total, the profile reports that it has
#: nothing to say. Brain's own floor is 30 labels for a model head; a
#: descriptive profile can be useful earlier, but not at n=3.
MIN_EXEMPLARS = 15

#: A trait needs this many winners carrying it before its lift is reported
#: as meaningful rather than as a coincidence.
MIN_TRAIT_SUPPORT = 4

#: Share of the pool treated as its winners.
EXEMPLAR_QUANTILE = 0.10

_NUMBER_RE = re.compile(r"\d")
_HASHTAG_RE = re.compile(r"#\w+")
_SECOND_PERSON_RE = re.compile(
    r"\b(you|your|tu|tú|tus|te|ti|usted)\b", re.IGNORECASE
)
_SUPERLATIVE_RE = re.compile(
    r"\b(m[áa]s|mejor|peor|[úu]ltim[oa]|primer[oa]?|nunca"
    r"|jam[áa]s|most|best|worst|never|only)\b",
    re.IGNORECASE,
)
_QUESTION_RE = re.compile(r"[?¿]")


# -- Title traits ------------------------------------------------------
# Each is a yes/no question about a headline, chosen because the clip
# writer can act on the answer. Kept readable: the profile is meant to be
# pasted into a prompt and understood by a human.


def title_traits(title: str) -> dict[str, bool]:
    """The yes/no shape of one headline."""
    text = (title or "").strip()
    bare = _HASHTAG_RE.sub("", text).strip()
    words = [w for w in re.split(r"\s+", bare) if w]
    letters = [c for c in bare if c.isalpha()]
    upper = [c for c in letters if c.isupper()]
    return {
        "question": bool(_QUESTION_RE.search(bare)),
        "has_number": bool(_NUMBER_RE.search(bare)),
        "has_hashtag": bool(_HASHTAG_RE.search(text)),
        "has_colon": ":" in bare,
        "second_person": bool(_SECOND_PERSON_RE.search(bare)),
        "superlative": bool(_SUPERLATIVE_RE.search(bare)),
        "short_title": len(words) <= 7,
        "long_title": len(words) >= 13,
        "shouty": bool(letters) and (len(upper) / len(letters)) > 0.3,
    }


TRAIT_NAMES = tuple(sorted(title_traits("").keys()))


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _quantile(values: list[float], q: float) -> float:
    """Linear-interpolated quantile; `values` need not be sorted."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    pos = max(0.0, min(1.0, q)) * (len(ordered) - 1)
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    frac = pos - low
    return float(ordered[low] * (1.0 - frac) + ordered[high] * frac)


# -- Loading -----------------------------------------------------------


def _is_clip_like(video: dict[str, Any]) -> bool:
    """A short, by the cache's own flag or by duration under three minutes."""
    if video.get("is_short"):
        return True
    duration = video.get("duration_seconds")
    try:
        return duration is not None and 0 < float(duration) <= 180
    except (TypeError, ValueError):
        return False


def _load_accounts(path: str) -> list[dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    accounts = data.get("accounts")
    if not isinstance(accounts, list):
        return []
    return [a for a in accounts if isinstance(a, dict)]


def normalized_shorts(
    accounts: Iterable[dict[str, Any]], origin: str = "reference"
) -> list[dict[str, Any]]:
    """Every short, tagged with its views over its own channel's median.

    Normalising per channel is what lets a small channel and a large one
    sit in the same pool. A channel without enough shorts to have a
    trustworthy median is skipped entirely.
    """
    rows: list[dict[str, Any]] = []
    for account in accounts:
        videos = account.get("videos")
        if not isinstance(videos, list):
            continue
        shorts = []
        for video in videos:
            if not isinstance(video, dict) or not _is_clip_like(video):
                continue
            try:
                views = float(video.get("view_count") or 0)
            except (TypeError, ValueError):
                continue
            if views <= 0:
                continue
            shorts.append((video, views))
        if len(shorts) < MIN_SHORTS_PER_CHANNEL:
            continue
        median = _median([views for _, views in shorts])
        if median <= 0:
            continue
        for video, views in shorts:
            raw_duration = video.get("duration_seconds")
            try:
                duration = float(raw_duration) if raw_duration is not None else None
            except (TypeError, ValueError):
                duration = None
            rows.append(
                {
                    "origin": origin,
                    "channel": str(
                        account.get("nickname") or account.get("youtube_handle") or ""
                    ),
                    "title": str(video.get("title") or ""),
                    "duration_seconds": duration,
                    "views": views,
                    "normalized_views": views / median,
                }
            )
    return rows


def collect_shorts() -> list[dict[str, Any]]:
    """The whole short-form pool: reference channels plus our own."""
    reference = os.path.join(clips_config.root_dir(), ".mp", "brain", "reference_channels.json")
    own = os.path.join(clips_config.root_dir(), ".mp", "youtube.json")
    return normalized_shorts(_load_accounts(reference), "reference") + normalized_shorts(
        _load_accounts(own), "own"
    )


# -- The profile -------------------------------------------------------


def build_profile(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Describe what separates the top decile from the bottom half.

    Pure: takes the pool, returns the profile. Split out from `profile()`
    so it can be tested on a fixture without touching the cache.
    """
    usable = [r for r in rows if r.get("normalized_views") is not None]
    total = len(usable)
    if total < MIN_EXEMPLARS * 2:
        return {
            "ready": False,
            "reason": (
                f"only {total} shorts with view data in the pool; need about "
                f"{MIN_EXEMPLARS * 2} before winners and losers can be told apart"
            ),
            "sample_size": total,
        }

    ordered = sorted(usable, key=lambda r: r["normalized_views"], reverse=True)
    take = max(MIN_EXEMPLARS, int(round(total * EXEMPLAR_QUANTILE)))
    exemplars = ordered[:take]
    baseline = ordered[len(ordered) // 2:]

    durations = [
        r["duration_seconds"]
        for r in exemplars
        if r.get("duration_seconds") and r["duration_seconds"] > 0
    ]
    duration_block: dict[str, Any] = {"sample_size": len(durations)}
    if len(durations) >= MIN_TRAIT_SUPPORT:
        duration_block.update(
            {
                "p25": round(_quantile(durations, 0.25), 1),
                "median": round(_quantile(durations, 0.50), 1),
                "p75": round(_quantile(durations, 0.75), 1),
                "min": round(min(durations), 1),
                "max": round(max(durations), 1),
            }
        )

    exemplar_traits = [title_traits(r["title"]) for r in exemplars]
    baseline_traits = [title_traits(r["title"]) for r in baseline]

    traits: dict[str, Any] = {}
    for name in TRAIT_NAMES:
        win_hits = sum(1 for t in exemplar_traits if t.get(name))
        base_hits = sum(1 for t in baseline_traits if t.get(name))
        win_share = win_hits / len(exemplars) if exemplars else 0.0
        base_share = base_hits / len(baseline) if baseline else 0.0
        # Laplace-smoothed, so a trait no loser carries does not produce an
        # infinite lift off a single winner.
        lift = ((win_hits + 1) / (len(exemplars) + 2)) / (
            (base_hits + 1) / (len(baseline) + 2)
        )
        traits[name] = {
            "winner_share": round(win_share, 3),
            "baseline_share": round(base_share, 3),
            "lift": round(lift, 2),
            "support": win_hits,
            # Only a trait carried by enough winners, and clearly more or
            # less common among them, is worth acting on.
            "meaningful": win_hits >= MIN_TRAIT_SUPPORT
            and (lift >= 1.25 or lift <= 0.8),
        }

    word_counts = [
        float(len([w for w in re.split(r"\s+", _HASHTAG_RE.sub("", r["title"]).strip()) if w]))
        for r in exemplars
    ]

    return {
        "ready": True,
        "sample_size": total,
        "exemplars": len(exemplars),
        "baseline": len(baseline),
        "channels": sorted({r["channel"] for r in usable if r["channel"]}),
        "duration_seconds": duration_block,
        "title_words": {
            "median": round(_quantile(word_counts, 0.5), 1),
            "p25": round(_quantile(word_counts, 0.25), 1),
            "p75": round(_quantile(word_counts, 0.75), 1),
        },
        "title_traits": traits,
        "winning_titles": [r["title"] for r in exemplars[:20] if r["title"]],
    }


def history_files() -> list[str]:
    """The channel histories the profile is learned from."""
    root = clips_config.root_dir()
    return [
        os.path.join(root, ".mp", "brain", "reference_channels.json"),
        os.path.join(root, ".mp", "youtube.json"),
    ]


def is_stale(profile_path: str, sources: Iterable[str] | None = None) -> bool:
    """Whether any history file changed after the profile was built.

    Without this, importing a new reference channel would change nothing:
    the cached profile would keep answering from the old pool, and "it
    learns from what wins" would quietly stop being true.
    """
    try:
        built_at = os.path.getmtime(profile_path)
    except OSError:
        return True
    for source in sources if sources is not None else history_files():
        try:
            if os.path.getmtime(source) > built_at:
                return True
        except OSError:
            continue
    return False


def profile(refresh: bool = False) -> dict[str, Any]:
    """The trend profile, cached on disk, rebuilt when the history changes."""
    path = clips_config.clips_file(clips_config.TREND_PROFILE_FILE)
    if not refresh and not is_stale(path) and os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as file:
                cached = json.load(file)
            if isinstance(cached, dict):
                return cached
        except (OSError, ValueError):
            pass
    built = build_profile(collect_shorts())
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as file:
        json.dump(built, file, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return built


#: A target window narrower than this leaves the segment builder nothing
#: to choose between, because candidates are whole sentences and a
#: sentence is several seconds long.
MIN_WINDOW_WIDTH = 15.0


def duration_window(prof: dict[str, Any] | None = None) -> tuple[float, float]:
    """The clip length to aim for: what wins, clamped to what we publish.

    The reference channels count two-minute videos as shorts, so their p75
    can land well above our ceiling. Clamping alone would collapse the
    window to a couple of seconds, so when the profile points above the
    ceiling we keep the width and slide the window down against it: aim at
    the longest clip we are willing to publish, not at an empty range.
    """
    floor = float(clips_config.setting("min_seconds"))
    ceiling = float(clips_config.setting("max_seconds"))
    if ceiling <= floor:
        return floor, floor + MIN_WINDOW_WIDTH

    block = ((prof or {}).get("duration_seconds") or {}) if prof else {}
    low, high = block.get("p25"), block.get("p75")
    if not (
        isinstance(low, (int, float))
        and isinstance(high, (int, float))
        and high > low > 0
    ):
        return floor, ceiling

    low, high = float(low), float(high)
    high = min(high, ceiling)
    low = max(floor, min(low, high - MIN_WINDOW_WIDTH))
    if high - low < MIN_WINDOW_WIDTH:
        high = min(ceiling, low + MIN_WINDOW_WIDTH)
    return low, high


#: How each trait reads as an instruction, for the prompt brief.
TRAIT_LABELS = {
    "question": "phrase the title as a question",
    "has_number": "put a concrete number in the title",
    "has_hashtag": "end the title with topic hashtags",
    "has_colon": "use a 'Subject: payoff' title",
    "second_person": "address the viewer directly",
    "superlative": "use a superlative (most/never/only)",
    "short_title": "keep the title under 8 words",
    "long_title": "write a long, 13+ word title",
    "shouty": "use heavy capitalisation",
}


def prompt_brief(prof: dict[str, Any]) -> str:
    """The profile as a few lines a language model can actually use."""
    if not prof.get("ready"):
        return "No trend profile yet: " + str(prof.get("reason", "not enough data")) + "."
    lines: list[str] = []
    duration = prof.get("duration_seconds") or {}
    if duration.get("median"):
        lines.append(
            f"- Winning clips on comparable channels run "
            f"{duration.get('p25')}-{duration.get('p75')}s "
            f"(median {duration.get('median')}s)."
        )
    words = prof.get("title_words") or {}
    if words.get("median"):
        lines.append(f"- Their titles run about {words.get('median')} words.")
    for name, stats in sorted(
        (prof.get("title_traits") or {}).items(),
        key=lambda item: -abs(item[1].get("lift", 1.0) - 1.0),
    ):
        if not stats.get("meaningful"):
            continue
        label = TRAIT_LABELS.get(name, name)
        win = int(stats["winner_share"] * 100)
        base = int(stats["baseline_share"] * 100)
        if stats["lift"] >= 1.25:
            lines.append(
                f"- DO: {label} - {win}% of winners vs {base}% of the rest "
                f"(x{stats['lift']})."
            )
        else:
            lines.append(
                f"- AVOID: {label} - only {win}% of winners vs {base}% of the rest "
                f"(x{stats['lift']})."
            )
    if len(lines) <= 2:
        lines.append("- Nothing else separates winners from the rest in this pool yet.")
    return "\n".join(lines)
