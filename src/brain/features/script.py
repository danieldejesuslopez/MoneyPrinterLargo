"""Script structure features (spec §18, §19).

These describe the *shape* of a narration, not its meaning: how dense the
facts are, how often something is revealed, how fast the words have to be
spoken to fit the target duration. Shape is what the retention curve
responds to, and unlike topic quality it is measurable without a model.

All of them return None when the candidate has no script yet, so the same
feature set serves both topic-stage ranking (script unknown, low coverage,
low confidence) and pre-render gating (script known, full coverage).
"""

from __future__ import annotations

from ..schema import Candidate, ChannelContext
from . import lexicon as lx
from .base import PRE_PUBLISH, FeatureSpec, safe_ratio

#: Words per second a narrator reaches at a normal pace. Used to turn
#: "words of script" into "seconds of video" when no TTS has run yet.
WORDS_PER_SECOND = 2.6


def _tokens(candidate: Candidate) -> list[str]:
    return lx.tokens(candidate.script) if candidate.script else []


def _density(count: int, word_count: int) -> float | None:
    """Markers per 100 words — length-independent, so long and short scripts
    are comparable."""
    if not word_count:
        return None
    return 100.0 * count / word_count


def script_words(c: Candidate, _: ChannelContext) -> float | None:
    words = _tokens(c)
    return float(len(words)) if words else None


def script_sentences(c: Candidate, _: ChannelContext) -> float | None:
    return float(len(lx.sentences(c.script))) if c.script else None


def script_avg_sentence_words(c: Candidate, _: ChannelContext) -> float | None:
    parts = lx.sentences(c.script)
    if not parts:
        return None
    return safe_ratio(len(_tokens(c)), len(parts))


def script_sentence_variance(c: Candidate, _: ChannelContext) -> float | None:
    """Spread of sentence lengths — flat rhythm is a known retention killer."""
    lengths = [len(lx.tokens(s)) for s in lx.sentences(c.script)]
    lengths = [n for n in lengths if n]
    if len(lengths) < 2:
        return None
    mean = sum(lengths) / len(lengths)
    variance = sum((n - mean) ** 2 for n in lengths) / len(lengths)
    return variance ** 0.5


def script_first_sentence_words(c: Candidate, _: ChannelContext) -> float | None:
    """Length of the opening line — the part that decides the first drop."""
    parts = lx.sentences(c.script)
    return float(len(lx.tokens(parts[0]))) if parts else None


def script_question_density(c: Candidate, _: ChannelContext) -> float | None:
    if not c.script:
        return None
    return _density(c.script.count("?") + c.script.count("¿"), len(_tokens(c)))


def script_number_density(c: Candidate, _: ChannelContext) -> float | None:
    words = _tokens(c)
    if not words:
        return None
    return _density(sum(1 for w in words if any(ch.isdigit() for ch in w)), len(words))


def script_reveal_density(c: Candidate, _: ChannelContext) -> float | None:
    words = _tokens(c)
    if not words:
        return None
    return _density(lx.count_markers(c.script, lx.REVEAL), len(words))


def script_open_loops(c: Candidate, _: ChannelContext) -> float | None:
    if not c.script:
        return None
    return float(lx.count_markers(c.script, lx.OPEN_LOOP))


def script_cta_count(c: Candidate, _: ChannelContext) -> float | None:
    if not c.script:
        return None
    return float(lx.count_markers(c.script, lx.CTA))


def script_transition_density(c: Candidate, _: ChannelContext) -> float | None:
    words = _tokens(c)
    if not words:
        return None
    return _density(lx.count_markers(c.script, lx.TRANSITION), len(words))


def script_filler_density(c: Candidate, _: ChannelContext) -> float | None:
    words = _tokens(c)
    if not words:
        return None
    return _density(lx.count_markers(c.script, lx.FILLER), len(words))


def script_emotion_density(c: Candidate, _: ChannelContext) -> float | None:
    words = _tokens(c)
    if not words:
        return None
    return _density(lx.count_markers(c.script, lx.EMOTION), len(words))


def script_curiosity_density(c: Candidate, _: ChannelContext) -> float | None:
    words = _tokens(c)
    if not words:
        return None
    hits = lx.count_markers(c.script, lx.CURIOSITY) + lx.count_markers(
        c.script, lx.CURIOSITY_PHRASES
    )
    return _density(hits, len(words))


def script_lexical_variety(c: Candidate, _: ChannelContext) -> float | None:
    """Type/token ratio — a script that keeps repeating itself sounds thin."""
    words = _tokens(c)
    if len(words) < 20:
        return None
    return len(set(words)) / len(words)


def script_seconds_to_first_number(c: Candidate, _: ChannelContext) -> float | None:
    """Estimated seconds before the first concrete figure lands.

    A proxy for "seconds until first payoff" (spec §18) that needs no LLM:
    the first number is usually the first verifiable claim.
    """
    words = _tokens(c)
    if not words:
        return None
    for index, word in enumerate(words):
        if any(ch.isdigit() for ch in word):
            return index / WORDS_PER_SECOND
    return float(len(words)) / WORDS_PER_SECOND


def script_words_per_second(c: Candidate, _: ChannelContext) -> float | None:
    """Required speaking rate to hit the target duration.

    Far above ~3.2 means the script is too long for its slot and will be
    rushed; far below ~2.0 means dead air the editor has to pad.
    """
    words = _tokens(c)
    if not words or not c.target_duration_seconds:
        return None
    return safe_ratio(len(words), c.target_duration_seconds)


SPECS: tuple[FeatureSpec, ...] = (
    FeatureSpec("script_words", "script", script_words, PRE_PUBLISH, 0.0,
                "Script length in words"),
    FeatureSpec("script_sentences", "script", script_sentences, PRE_PUBLISH, 0.0,
                "Number of sentences"),
    FeatureSpec("script_avg_sentence_words", "script", script_avg_sentence_words,
                PRE_PUBLISH, 14.0, "Average sentence length"),
    FeatureSpec("script_sentence_variance", "script", script_sentence_variance,
                PRE_PUBLISH, 5.0, "Std-dev of sentence length (rhythm)"),
    FeatureSpec("script_first_sentence_words", "script", script_first_sentence_words,
                PRE_PUBLISH, 12.0, "Opening line length"),
    FeatureSpec("script_question_density", "script", script_question_density,
                PRE_PUBLISH, 0.0, "Questions per 100 words"),
    FeatureSpec("script_number_density", "script", script_number_density,
                PRE_PUBLISH, 0.0, "Numbers per 100 words (fact density)"),
    FeatureSpec("script_reveal_density", "script", script_reveal_density,
                PRE_PUBLISH, 0.0, "Reveal markers per 100 words"),
    FeatureSpec("script_open_loops", "script", script_open_loops, PRE_PUBLISH, 0.0,
                "Open-loop promises"),
    FeatureSpec("script_cta_count", "script", script_cta_count, PRE_PUBLISH, 0.0,
                "Calls to action"),
    FeatureSpec("script_transition_density", "script", script_transition_density,
                PRE_PUBLISH, 0.0, "Transitions per 100 words"),
    FeatureSpec("script_filler_density", "script", script_filler_density,
                PRE_PUBLISH, 0.0, "Filler words per 100 words"),
    FeatureSpec("script_emotion_density", "script", script_emotion_density,
                PRE_PUBLISH, 0.0, "Emotional words per 100 words"),
    FeatureSpec("script_curiosity_density", "script", script_curiosity_density,
                PRE_PUBLISH, 0.0, "Curiosity markers per 100 words"),
    FeatureSpec("script_lexical_variety", "script", script_lexical_variety,
                PRE_PUBLISH, 0.5, "Type/token ratio"),
    FeatureSpec("script_seconds_to_first_number", "script",
                script_seconds_to_first_number, PRE_PUBLISH, 15.0,
                "Estimated seconds until the first concrete figure"),
    FeatureSpec("script_words_per_second", "script", script_words_per_second,
                PRE_PUBLISH, 2.6, "Required speaking rate for the target duration"),
)
