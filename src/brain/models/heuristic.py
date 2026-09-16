"""The cold-start scorer: rules that work before any model can be trained.

Every channel starts at zero rows, and the plan is explicit that a neural
network trained on 20 videos is worse than useless (spec §86, §156). So
Brain's floor is a transparent rule table, and the rules never go away: once
a model exists they keep contributing a fixed share of the blended score
(spec §137) and keep acting as the guardrail that catches inputs a
regression fitted to a small sample would happily rank first.

Each rule declares the features it reads. A rule whose inputs were *missing*
on this candidate does not fire at all — scoring a topic-stage candidate on
a defaulted script metric would invent signal that nobody measured.

Points are on a -1.5..+1.5 scale around a neutral 5.0, so a candidate that
trips nothing scores 5.0 and the range stays inside 0-10.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from ..schema import Factor

Scorer = Callable[[dict[str, float]], float]

NEUTRAL_SCORE = 5.0


@dataclass(frozen=True)
class Rule:
    """One interpretable scoring rule.

    Two-sided rules carry `negative_detail` as well, because the same
    measurement reads differently in each column: "distance from everything
    the channel published" is praise as a plus and nonsense as a minus,
    where "too close to what the channel already published" is the thing
    the reader actually needs to hear.
    """

    name: str
    inputs: tuple[str, ...]
    fn: Scorer
    detail: str = ""
    negative_detail: str = ""

    def applies(self, missing: set[str]) -> bool:
        return not any(name in missing for name in self.inputs)

    def describe(self, points: float) -> str:
        if points < 0 and self.negative_detail:
            return self.negative_detail
        return self.detail


def _band(value: float, low: float, high: float, reward: float = 1.0) -> float:
    """+reward inside [low, high], falling off linearly outside it."""
    if low <= value <= high:
        return reward
    span = (high - low) or 1.0
    distance = (low - value) if value < low else (value - high)
    return max(-reward, reward - 2.0 * reward * (distance / span))


RULES: tuple[Rule, ...] = (
    Rule(
        "curiosity_gap", ("title_curiosity",),
        lambda f: min(1.2, 0.6 * f["title_curiosity"]),
        "Headline opens a curiosity gap",
    ),
    Rule(
        "specificity", ("title_specificity",),
        lambda f: min(1.0, 0.45 * f["title_specificity"]),
        "Headline names something concrete",
    ),
    Rule(
        "headline_length", ("title_chars",),
        lambda f: _band(f["title_chars"], 35.0, 75.0, 0.8),
        "Headline length is in the readable band",
        "Headline is too short or too long to read at a glance",
    ),
    Rule(
        "headline_number", ("title_has_number",),
        lambda f: 0.4 * f["title_has_number"],
        "Headline contains a concrete figure",
    ),
    Rule(
        "shouting", ("title_uppercase_ratio",),
        lambda f: -1.2 if f["title_uppercase_ratio"] > 0.4 else 0.0,
        "Headline is mostly uppercase",
    ),
    Rule(
        "superlative_overload", ("title_superlative",),
        lambda f: -0.5 * max(0.0, f["title_superlative"] - 2.0),
        "Stacked superlatives with nothing concrete behind them",
    ),
    Rule(
        "novelty", ("topic_novelty",),
        lambda f: 1.5 * (f["topic_novelty"] - 0.5),
        "A fresh angle for this channel",
        "Close to what the channel has already published",
    ),
    Rule(
        "recent_repetition", ("topic_recent_similarity",),
        lambda f: -2.0 * max(0.0, f["topic_recent_similarity"] - 0.35),
        "Overlaps the last few uploads",
    ),
    Rule(
        "winner_family", ("topic_winner_similarity",),
        lambda f: 1.5 * f["topic_winner_similarity"],
        "Close to topics that over-performed on this channel",
    ),
    Rule(
        "loser_family", ("topic_loser_similarity",),
        lambda f: -1.5 * f["topic_loser_similarity"],
        "Close to topics that under-performed on this channel",
    ),
    Rule(
        "hook_anchored", ("hook_subject_anchor",),
        lambda f: 1.2 * (f["hook_subject_anchor"] - 0.15),
        "Hook names the subject instead of promising vaguely",
        "Hook promises vaguely without naming its subject",
    ),
    Rule(
        "hook_curiosity", ("hook_curiosity",),
        lambda f: min(0.8, 0.4 * f["hook_curiosity"]),
        "Hook opens a curiosity gap",
    ),
    Rule(
        "pacing", ("script_words_per_second",),
        lambda f: _band(f["script_words_per_second"], 2.2, 3.1, 1.0),
        "Speaking rate fits the target duration",
        "Script does not fit its slot: rushed or padded",
    ),
    Rule(
        "fact_density", ("script_number_density",),
        lambda f: min(0.8, 0.35 * f["script_number_density"]),
        "Script carries concrete figures",
    ),
    Rule(
        "reveal_rhythm", ("script_reveal_density",),
        lambda f: _band(f["script_reveal_density"], 1.5, 6.0, 0.8),
        "Reveals are spaced through the script",
        "Reveals are bunched up or missing",
    ),
    Rule(
        "filler", ("script_filler_density",),
        lambda f: -0.4 * max(0.0, f["script_filler_density"] - 2.0),
        "Filler words dilute the narration",
    ),
    Rule(
        "flat_rhythm", ("script_sentence_variance",),
        lambda f: -0.8 if f["script_sentence_variance"] < 2.0 else 0.0,
        "Every sentence is the same length",
    ),
    Rule(
        "slow_payoff", ("script_seconds_to_first_number",),
        lambda f: -0.6 if f["script_seconds_to_first_number"] > 25.0 else 0.0,
        "Nothing concrete lands in the opening",
    ),
    Rule(
        "cta_spam", ("script_cta_count",),
        lambda f: -0.4 * max(0.0, f["script_cta_count"] - 2.0),
        "Repeated calls to action",
    ),
    Rule(
        "lab_retention", ("lab_retention_score",),
        lambda f: 0.35 * (f["lab_retention_score"] - 7.0),
        "RetentionLab rates this above its bar",
        "RetentionLab rates this below its bar",
    ),
    Rule(
        "lab_hook", ("lab_hook_score",),
        lambda f: 0.3 * (f["lab_hook_score"] - 7.0),
        "The selected hook scored well in the lab",
        "The selected hook scored poorly in the lab",
    ),
    Rule(
        "lab_first_image", ("lab_first_image_score",),
        lambda f: 0.25 * (f["lab_first_image_score"] - 7.0),
        "The opening frame scored well in the lab",
        "The opening frame scored poorly in the lab",
    ),
    Rule(
        "thumbnail_contrast", ("thumb_contrast",),
        lambda f: _band(f["thumb_contrast"], 0.15, 0.45, 0.5),
        "Thumbnail has enough tonal separation to read small",
        "Thumbnail is too flat to read at phone size",
    ),
    Rule(
        "thumbnail_busyness", ("thumb_edge_density",),
        lambda f: -0.6 if f["thumb_edge_density"] > 0.35 else 0.0,
        "Thumbnail is too busy to read at phone size",
    ),
    Rule(
        "format_fit", ("kind_matches_channel",),
        lambda f: 0.8 * (f["kind_matches_channel"] - 0.5),
        "Format matches what this channel's audience subscribed for",
        "Format is not what this channel's audience subscribed for",
    ),
    Rule(
        "cadence_gap", ("channel_days_since_last_upload",),
        lambda f: -0.5 if f["channel_days_since_last_upload"] > 14.0 else 0.0,
        "Long silence since the previous upload",
    ),
)


def score(
    features: dict[str, float],
    missing: Iterable[str] = (),
    rules: tuple[Rule, ...] = RULES,
) -> tuple[float, list[Factor]]:
    """Score a candidate from its features. Returns `(0-10, factors)`."""
    absent = set(missing or ())
    total = NEUTRAL_SCORE
    factors: list[Factor] = []

    for rule in rules:
        if not rule.applies(absent):
            continue
        try:
            points = float(rule.fn(features))
        except Exception:
            continue
        if abs(points) < 1e-9:
            continue
        points = max(-1.5, min(1.5, points))
        total += points
        factors.append(
            Factor(name=rule.name, contribution=points, detail=rule.describe(points))
        )

    factors.sort(key=lambda factor: abs(factor.contribution), reverse=True)
    return max(0.0, min(10.0, total)), factors


def rule_names() -> tuple[str, ...]:
    return tuple(rule.name for rule in RULES)
