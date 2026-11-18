"""Turning a word-level transcript into clip candidates.

Pure functions: no file I/O, no network, no ffmpeg. Everything here takes
timed words and returns timed spans, which is what makes the selection
logic testable without rendering a frame (`tests/test_clips_segments.py`).

The one rule that shapes the whole design: **a clip never starts or ends
mid-sentence.** A cut that lands inside a sentence sounds broken no matter
how good the content is, so candidates are not arbitrary sliding windows.
They are runs of consecutive whole sentences, and the length target is met
by choosing how many sentences to include. Sentence boundaries come from
punctuation and from silence, so a transcript with no punctuation at all
still produces sane cuts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence


#: A pause at least this long ends a sentence even without punctuation.
#: Whisper omits punctuation on some languages and models, and a speaker's
#: breath is a better boundary than a guess.
DEFAULT_SILENCE_GAP = 0.65

#: Sentence-ending punctuation, including the Spanish inverted openers
#: that must *not* end one.
_SENTENCE_END = re.compile(r"[.!?…]+[\"'”»)\]]*$")

#: Trailing-fragment guard: a "sentence" this short is a stray word, and is
#: merged into its neighbour rather than being treated as a boundary.
MIN_SENTENCE_WORDS = 2


@dataclass(frozen=True)
class Word:
    """One timed word from the transcript."""

    start: float
    end: float
    text: str

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Word":
        start = float(raw.get("start") or 0.0)
        end = float(raw.get("end") or start)
        text = str(raw.get("word") or raw.get("text") or "").strip()
        return cls(start=max(0.0, start), end=max(start, end), text=text)


@dataclass
class Sentence:
    """A run of words that ends on punctuation or on a pause."""

    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class Window:
    """A clip candidate: whole sentences, with its place in the source."""

    start: float
    end: float
    text: str
    first_sentence: int
    last_sentence: int
    #: Set by the scorer, not here. Kept on the window so selection has
    #: everything it needs in one object.
    score: float = 0.0
    scores: dict[str, Any] = field(default_factory=dict)
    title: str = ""
    reason: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def parse_words(raw_words: Iterable[dict[str, Any]]) -> list[Word]:
    """Timed words from the transcript shape the rest of the repo uses.

    Drops empty tokens and anything whose timing is unusable, and sorts by
    start time so a provider that returns segments out of order cannot
    corrupt the boundaries.
    """
    words = [Word.from_dict(raw) for raw in raw_words or []]
    words = [w for w in words if w.text and w.end > w.start]
    return sorted(words, key=lambda w: (w.start, w.end))


def group_sentences(
    words: Sequence[Word], silence_gap: float = DEFAULT_SILENCE_GAP
) -> list[Sentence]:
    """Split timed words into sentences on punctuation or on a pause."""
    sentences: list[Sentence] = []
    current: list[Word] = []

    def flush() -> None:
        if not current:
            return
        sentences.append(
            Sentence(
                start=current[0].start,
                end=current[-1].end,
                text=" ".join(w.text for w in current).strip(),
                words=list(current),
            )
        )
        current.clear()

    for index, word in enumerate(words):
        if current and (word.start - current[-1].end) >= silence_gap:
            flush()
        current.append(word)
        if _SENTENCE_END.search(word.text):
            flush()
            continue
        # Guard against a runaway sentence when there is no punctuation and
        # the speaker never pauses: 40 words is already a long breath.
        if len(current) >= 40:
            flush()
    flush()

    return _merge_fragments(sentences)


def _merge_fragments(sentences: list[Sentence]) -> list[Sentence]:
    """Fold one-word fragments into the neighbour they belong to."""
    if len(sentences) < 2:
        return sentences
    merged: list[Sentence] = []
    for sentence in sentences:
        if (
            merged
            and len(sentence.words) < MIN_SENTENCE_WORDS
            and not _SENTENCE_END.search(merged[-1].text)
        ):
            previous = merged[-1]
            previous.words.extend(sentence.words)
            previous.end = sentence.end
            previous.text = (previous.text + " " + sentence.text).strip()
            continue
        merged.append(sentence)
    return merged


def build_windows(
    sentences: Sequence[Sentence],
    min_seconds: float,
    max_seconds: float,
    max_windows: int = 400,
) -> list[Window]:
    """Every run of consecutive sentences whose length fits the target.

    One window per starting sentence: the longest run that still fits
    under `max_seconds`. Taking the longest fitting run rather than every
    run keeps the candidate set to one per start — an O(n) set instead of
    an O(n^2) one — and a longer run carries more of the point being made,
    which is what a self-contained clip needs.
    """
    if min_seconds > max_seconds:
        min_seconds, max_seconds = max_seconds, min_seconds

    windows: list[Window] = []
    for first in range(len(sentences)):
        start = sentences[first].start
        last = first
        for candidate_last in range(first, len(sentences)):
            if sentences[candidate_last].end - start > max_seconds:
                break
            last = candidate_last
        span = sentences[last].end - start
        if span < min_seconds:
            continue
        windows.append(
            Window(
                start=start,
                end=sentences[last].end,
                text=" ".join(s.text for s in sentences[first:last + 1]).strip(),
                first_sentence=first,
                last_sentence=last,
            )
        )
        if len(windows) >= max_windows:
            break
    return windows


def overlap_ratio(a: Window, b: Window) -> float:
    """Shared time as a share of the *shorter* window.

    Measuring against the shorter one is what makes a small clip sitting
    entirely inside a larger one score 1.0, which is the honest answer:
    publishing both would publish the same moment twice.
    """
    intersection = min(a.end, b.end) - max(a.start, b.start)
    if intersection <= 0:
        return 0.0
    shortest = min(a.duration, b.duration)
    if shortest <= 0:
        return 1.0
    return intersection / shortest


def select_clips(
    windows: Sequence[Window], count: int, max_overlap: float = 0.25
) -> list[Window]:
    """The best `count` windows that do not repeat each other.

    Greedy by score, skipping any window that overlaps an already-kept one
    by more than `max_overlap`. Greedy is the right call here: the scores
    are noisy estimates, so the gain from a globally optimal packing is
    smaller than the noise it would be optimising against.
    """
    if count <= 0:
        return []
    kept: list[Window] = []
    for window in sorted(windows, key=lambda w: (-w.score, w.start)):
        if any(overlap_ratio(window, chosen) > max_overlap for chosen in kept):
            continue
        kept.append(window)
        if len(kept) >= count:
            break
    return sorted(kept, key=lambda w: w.start)


def words_in_span(
    words: Sequence[Word], start: float, end: float
) -> list[Word]:
    """The words inside a span, re-timed to start at zero.

    Subtitles are burned onto the extracted clip, whose timeline starts at
    zero, not at the moment in the source where the clip began.
    """
    picked = [w for w in words if w.end > start and w.start < end]
    return [
        Word(
            start=max(0.0, w.start - start),
            end=max(0.0, min(w.end, end) - start),
            text=w.text,
        )
        for w in picked
    ]
