"""Title / topic / hook features (spec §16, §38, §39).

Version 0 is deliberately lexical, not embedding-based. A frozen sentence
encoder gives 384 dense dimensions; with a few hundred videos that is far
more parameters than signal, and none of them can be read back to the user
as "why this video". These ~20 interpretable counts feed both the linear
model and the `WHY?` panel, and `features/text_embeddings.py` can be added
later without changing the contract (spec §117: dependencies arrive only
when the data justifies them).

At topic stage a candidate has no title yet, so title features fall back to
the topic — in this pipeline the topic *is* the working headline that later
becomes a title, and ranking topics is the whole point of the stage.
"""

from __future__ import annotations

from ..schema import Candidate, ChannelContext
from . import lexicon as lx
from .base import PRE_PUBLISH, FeatureSpec


def headline(candidate: Candidate) -> str:
    """The best available headline text for this candidate."""
    return candidate.title or candidate.topic or ""


def _uppercase_ratio(text: str) -> float | None:
    letters = [c for c in str(text or "") if c.isalpha()]
    if not letters:
        return None
    return sum(1 for c in letters if c.isupper()) / len(letters)


def _curiosity(text: str) -> int:
    return lx.count_markers(text, lx.CURIOSITY) + lx.count_markers(
        text, lx.CURIOSITY_PHRASES
    )


# -- Title --------------------------------------------------------------


def title_chars(c: Candidate, _: ChannelContext) -> float | None:
    text = headline(c)
    return float(len(text)) if text else None


def title_words(c: Candidate, _: ChannelContext) -> float | None:
    text = headline(c)
    return float(len(lx.tokens(text))) if text else None


def title_has_number(c: Candidate, _: ChannelContext) -> float | None:
    text = headline(c)
    return float(lx.has_digit(text)) if text else None


def title_is_question(c: Candidate, _: ChannelContext) -> float | None:
    text = headline(c)
    if not text:
        return None
    return float("?" in text or text.strip().startswith("¿"))


def title_has_separator(c: Candidate, _: ChannelContext) -> float | None:
    """Colon/dash structure — "Subject: promise" is a distinct title shape."""
    text = headline(c)
    if not text:
        return None
    return float(any(sep in text for sep in (":", " - ", " — ", " | ")))


def title_uppercase_ratio(c: Candidate, _: ChannelContext) -> float | None:
    return _uppercase_ratio(headline(c))


def title_curiosity(c: Candidate, _: ChannelContext) -> float | None:
    text = headline(c)
    return float(_curiosity(text)) if text else None


def title_superlative(c: Candidate, _: ChannelContext) -> float | None:
    text = headline(c)
    return float(lx.count_markers(text, lx.SUPERLATIVE)) if text else None


def title_emotion(c: Candidate, _: ChannelContext) -> float | None:
    text = headline(c)
    return float(lx.count_markers(text, lx.EMOTION)) if text else None


def title_specificity(c: Candidate, _: ChannelContext) -> float | None:
    """Concrete anchors: units, dates, and capitalized names mid-sentence."""
    text = headline(c)
    if not text:
        return None
    units = lx.count_markers(text, lx.SPECIFICITY)
    words = str(text).split()
    proper = sum(1 for w in words[1:] if w[:1].isupper() and not w.isupper())
    return float(units + proper)


def title_second_person(c: Candidate, _: ChannelContext) -> float | None:
    text = headline(c)
    return float(lx.count_markers(text, lx.SECOND_PERSON)) if text else None


def title_content_density(c: Candidate, _: ChannelContext) -> float | None:
    """Share of distinctive (non-stopword) words — filler-heavy titles score low."""
    text = headline(c)
    if not text:
        return None
    all_tokens = lx.tokens(text)
    if not all_tokens:
        return None
    return len(lx.content_tokens(text)) / len(all_tokens)


def title_hashtags(c: Candidate, _: ChannelContext) -> float | None:
    text = headline(c)
    return float(len(lx.hashtags(text))) if text else None


def title_visible_chars(c: Candidate, _: ChannelContext) -> float | None:
    """Length of the headline *before* the hashtag tail.

    What the viewer actually reads in a crowded feed, which is not the same
    as `title_chars` on a channel that appends five tags to everything.
    """
    text = headline(c)
    return float(len(lx.visible_prefix(text))) if text else None


def title_emoji(c: Candidate, _: ChannelContext) -> float | None:
    text = headline(c)
    return float(lx.count_emoji(text)) if text else None


def title_caps_words(c: Candidate, _: ChannelContext) -> float | None:
    """Shouted words. Distinct from `title_uppercase_ratio`: one emphasised
    word in a normal headline is a device, an all-caps headline is noise."""
    text = headline(c)
    return float(lx.caps_words(text)) if text else None


def title_subject_prefix(c: Candidate, _: ChannelContext) -> float | None:
    """Characters before a leading colon - the "PSR J0737-3039: ..." shape.

    A named-subject prefix promises specificity; a long one spends the
    reader's attention before the hook arrives.
    """
    text = lx.visible_prefix(headline(c))
    if not text:
        return None
    index = text.find(":")
    return float(index) if 0 < index < 60 else 0.0


# -- Hook ---------------------------------------------------------------


def hook_words(c: Candidate, _: ChannelContext) -> float | None:
    return float(len(lx.tokens(c.hook))) if c.hook else None


def hook_is_question(c: Candidate, _: ChannelContext) -> float | None:
    if not c.hook:
        return None
    return float("?" in c.hook or c.hook.strip().startswith("¿"))


def hook_has_number(c: Candidate, _: ChannelContext) -> float | None:
    return float(lx.has_digit(c.hook)) if c.hook else None


def hook_curiosity(c: Candidate, _: ChannelContext) -> float | None:
    return float(_curiosity(c.hook)) if c.hook else None


def hook_emotion(c: Candidate, _: ChannelContext) -> float | None:
    return float(lx.count_markers(c.hook, lx.EMOTION)) if c.hook else None


def hook_subject_anchor(c: Candidate, _: ChannelContext) -> float | None:
    """Does the hook name the topic's subject, or is it generic hype?

    A hook that shares no distinctive word with its own topic ("Esto te va a
    sorprender") promises nothing specific — the failure mode `RetentionLab`
    already guards against when scoring hooks.
    """
    if not c.hook or not c.topic:
        return None
    return lx.jaccard(lx.content_tokens(c.hook), lx.content_tokens(c.topic))


# -- Description / tags -------------------------------------------------


def description_chars(c: Candidate, _: ChannelContext) -> float | None:
    return float(len(c.description)) if c.description else None


def tag_count(c: Candidate, _: ChannelContext) -> float | None:
    return float(len(c.tags)) if c.tags else None


def topic_words(c: Candidate, _: ChannelContext) -> float | None:
    return float(len(lx.tokens(c.topic))) if c.topic else None


SPECS: tuple[FeatureSpec, ...] = (
    FeatureSpec("title_chars", "text", title_chars, PRE_PUBLISH, 55.0,
                "Headline length in characters"),
    FeatureSpec("title_words", "text", title_words, PRE_PUBLISH, 9.0,
                "Headline length in words"),
    FeatureSpec("title_has_number", "text", title_has_number, PRE_PUBLISH, 0.0,
                "Headline contains a digit"),
    FeatureSpec("title_is_question", "text", title_is_question, PRE_PUBLISH, 0.0,
                "Headline is phrased as a question"),
    FeatureSpec("title_has_separator", "text", title_has_separator, PRE_PUBLISH, 0.0,
                "Headline uses a colon/dash structure"),
    FeatureSpec("title_uppercase_ratio", "text", title_uppercase_ratio, PRE_PUBLISH, 0.08,
                "Share of uppercase letters"),
    FeatureSpec("title_curiosity", "text", title_curiosity, PRE_PUBLISH, 0.0,
                "Curiosity-gap markers in the headline"),
    FeatureSpec("title_superlative", "text", title_superlative, PRE_PUBLISH, 0.0,
                "Superlatives and absolutes"),
    FeatureSpec("title_emotion", "text", title_emotion, PRE_PUBLISH, 0.0,
                "Emotional-intensity words"),
    FeatureSpec("title_specificity", "text", title_specificity, PRE_PUBLISH, 0.0,
                "Concrete anchors: units, dates, proper nouns"),
    FeatureSpec("title_second_person", "text", title_second_person, PRE_PUBLISH, 0.0,
                "Direct address to the viewer"),
    FeatureSpec("title_content_density", "text", title_content_density, PRE_PUBLISH, 0.5,
                "Share of distinctive (non-stopword) words"),
    FeatureSpec("hook_words", "text", hook_words, PRE_PUBLISH, 12.0,
                "Hook length in words"),
    FeatureSpec("hook_is_question", "text", hook_is_question, PRE_PUBLISH, 0.0,
                "Hook is phrased as a question"),
    FeatureSpec("hook_has_number", "text", hook_has_number, PRE_PUBLISH, 0.0,
                "Hook contains a digit"),
    FeatureSpec("hook_curiosity", "text", hook_curiosity, PRE_PUBLISH, 0.0,
                "Curiosity-gap markers in the hook"),
    FeatureSpec("hook_emotion", "text", hook_emotion, PRE_PUBLISH, 0.0,
                "Emotional-intensity words in the hook"),
    FeatureSpec("hook_subject_anchor", "text", hook_subject_anchor, PRE_PUBLISH, 0.0,
                "Overlap between hook and its own topic"),
    FeatureSpec("description_chars", "text", description_chars, PRE_PUBLISH, 0.0,
                "Description length"),
    FeatureSpec("tag_count", "text", tag_count, PRE_PUBLISH, 0.0,
                "Number of tags"),
    FeatureSpec("topic_words", "text", topic_words, PRE_PUBLISH, 7.0,
                "Topic length in words"),
    FeatureSpec("title_hashtags", "text", title_hashtags, PRE_PUBLISH, 0.0,
                "Number of hashtags in the headline"),
    FeatureSpec("title_visible_chars", "text", title_visible_chars, PRE_PUBLISH,
                45.0, "Headline length before the hashtag tail"),
    FeatureSpec("title_emoji", "text", title_emoji, PRE_PUBLISH, 0.0,
                "Emoji in the headline"),
    FeatureSpec("title_caps_words", "text", title_caps_words, PRE_PUBLISH, 0.0,
                "Fully-uppercase words"),
    FeatureSpec("title_subject_prefix", "text", title_subject_prefix, PRE_PUBLISH,
                0.0, "Characters before a leading colon"),
)
