"""Bilingual (ES/EN) word lists and text primitives for the feature layer.

The channels this repo drives publish in Spanish and English, often mixed
inside one account, so every lexical feature has to be language-agnostic by
construction: one set per concept, containing both languages, matched on
accent-stripped lowercase tokens.

Deliberately small and hand-picked. A 5000-word sentiment lexicon would look
more serious and predict less: with a few hundred labeled videos, every extra
feature is another chance to fit noise. These sets encode the handful of
patterns the retention labs in this repo already treat as meaningful.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

_TOKEN_RE = re.compile(r"[\w']+", re.UNICODE)
_SENTENCE_RE = re.compile(r"[^.!?\n]+[.!?]*")
_NUMBER_RE = re.compile(r"\d")


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", str(text or ""))
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def normalize(text: str) -> str:
    """Lowercase, accent-free, whitespace-collapsed."""
    return re.sub(r"\s+", " ", strip_accents(text).lower()).strip()


def tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(normalize(text))


def sentences(text: str) -> list[str]:
    """Split into sentences, dropping empties. Newlines end a sentence."""
    return [s.strip() for s in _SENTENCE_RE.findall(str(text or "")) if s.strip()]


def has_digit(text: str) -> bool:
    return bool(_NUMBER_RE.search(str(text or "")))


def hashtags(text: str) -> list[str]:
    """The #tags in a string, lowercased and without the #."""
    return [t.lower() for t in re.findall(r"#(\w{2,40})", str(text or ""))]


def visible_prefix(text: str) -> str:
    """The part of a title a viewer reads before the hashtag tail.

    These channels append "#espacio #misterio #astronomia" to almost every
    title. Those characters count toward the length YouTube truncates at but
    carry none of the promise, so measuring raw length treats a punchy
    headline with five tags as a long one.
    """
    raw = str(text or "")
    index = raw.find("#")
    return (raw[:index] if index > 0 else raw).strip()


def count_emoji(text: str) -> int:
    """Emoji and pictographic symbols in a string."""
    total = 0
    for char in str(text or ""):
        code = ord(char)
        if 0x1F000 <= code <= 0x1FAFF or 0x2600 <= code <= 0x27BF:
            total += 1
        elif unicodedata.category(char) == "So":
            total += 1
    return total


def caps_words(text: str) -> int:
    """Fully-uppercase words of 2+ letters - shouted emphasis, not shouting."""
    return sum(
        1 for word in re.findall(r"[^\W\d_]{2,}", str(text or ""), re.UNICODE)
        if word.isupper()
    )


def count_markers(text: str, markers: frozenset[str]) -> int:
    """Count marker hits in `text`, handling words and phrases alike.

    Single-word markers are matched against the token list (so "pero" does
    not fire inside "peronismo"); multi-word markers are matched against the
    space-joined token stream, which is equivalent to a word-boundary search
    and costs one string scan instead of a regex per marker.
    """
    token_list = tokens(text)
    if not token_list:
        return 0
    counts = Counter(token_list)
    stream = f" {' '.join(token_list)} "
    total = 0
    for marker in markers:
        if " " in marker:
            total += stream.count(f" {marker} ")
        else:
            total += counts.get(marker, 0)
    return total


# -- Concept sets -------------------------------------------------------

#: Words that open a curiosity gap without naming the payoff.
CURIOSITY = frozenset({
    "secreto", "secretos", "misterio", "misterios", "oculto", "oculta",
    "desconocido", "desconocida", "prohibido", "prohibida", "nadie",
    "jamas", "nunca", "realmente", "verdad", "verdadera", "descubrieron",
    "descubrio", "revelo", "revela", "revelacion", "enigma", "extrano",
    "extrana", "inexplicable", "increible", "sorprendente",
    "secret", "secrets", "mystery", "hidden", "unknown", "forbidden",
    "nobody", "never", "really", "truth", "true", "discovered", "revealed",
    "reveals", "enigma", "strange", "unexplained", "incredible",
    "surprising", "shocking",
})

#: Phrases that specifically promise a reveal ("what really happened").
CURIOSITY_PHRASES = frozenset({
    "lo que nadie", "lo que realmente", "lo que paso", "nadie sabe",
    "no quieren que", "hasta ahora", "por primera vez",
    "what nobody", "what really", "what happened", "no one knows",
    "they don't want", "until now", "for the first time",
})

#: Emotional intensity — fear, awe, scale.
EMOTION = frozenset({
    "aterrador", "aterradora", "terrible", "peligroso", "peligrosa",
    "mortal", "catastrofe", "desastre", "colapso", "muerte", "murio",
    "destruccion", "brutal", "salvaje", "impactante", "asombroso",
    "gigantesco", "colosal", "monstruoso", "infinito", "eterno",
    "terrifying", "terrible", "dangerous", "deadly", "catastrophe",
    "disaster", "collapse", "death", "died", "destruction", "brutal",
    "savage", "shocking", "amazing", "gigantic", "colossal", "monstrous",
    "infinite", "eternal", "horrifying",
})

#: Superlatives and absolutes — cheap intensity, useful but easy to burn.
SUPERLATIVE = frozenset({
    "mas", "mayor", "peor", "mejor", "primero", "primera", "ultimo",
    "ultima", "unico", "unica", "jamas", "siempre", "todo", "todos",
    "most", "biggest", "worst", "best", "first", "last", "only", "ever",
    "always", "every", "greatest", "largest",
})

#: Concrete-information markers — dates, units, named quantities.
SPECIFICITY = frozenset({
    "ano", "anos", "km", "kilometros", "metros", "millones", "miles",
    "grados", "toneladas", "siglo", "siglos", "dia", "dias", "horas",
    "minutos", "segundos", "veces",
    "year", "years", "km", "kilometers", "meters", "million", "billion",
    "thousand", "degrees", "tons", "century", "centuries", "day", "days",
    "hours", "minutes", "seconds", "times", "percent", "porcentaje",
})

#: Second-person address — pulls the viewer in.
SECOND_PERSON = frozenset({
    "tu", "tus", "te", "ti", "usted", "ustedes", "tuyo", "tuya",
    "you", "your", "yours",
})

#: Reveal / payoff markers inside a script.
REVEAL = frozenset({
    "pero", "sin embargo", "entonces", "resulta", "descubrieron",
    "descubrio", "revelo", "revela", "finalmente", "hasta que", "aunque",
    "but", "however", "then", "turns", "discovered", "revealed", "finally",
    "until", "although", "yet",
})

#: Open-loop markers — a promise the script has not paid off yet.
OPEN_LOOP = frozenset({
    "antes de", "en un momento", "ya veras", "espera", "primero",
    "lo importante", "presta atencion", "recuerda",
    "before", "in a moment", "wait", "first", "the important", "pay attention",
    "remember", "keep watching",
})

#: Calls to action.
CTA = frozenset({
    "suscribete", "suscribirte", "comenta", "comentarios", "like",
    "comparte", "sigueme", "canal", "campana",
    "subscribe", "comment", "comments", "share", "follow", "channel",
    "bell", "notification",
})

#: Transitions that keep narration moving between beats.
TRANSITION = frozenset({
    "ademas", "asi", "luego", "despues", "mientras", "porque", "por eso",
    "es decir", "de hecho", "en cambio", "ahora",
    "also", "so", "then", "after", "while", "because", "that is",
    "in fact", "instead", "now", "meanwhile",
})

#: Filler that dilutes narration density.
FILLER = frozenset({
    "basicamente", "obviamente", "realmente", "simplemente", "muy",
    "bastante", "algo", "cosa", "cosas", "bueno", "pues",
    "basically", "obviously", "really", "simply", "very", "quite",
    "something", "thing", "things", "well", "just",
})

#: Stopwords (ES + EN) used when extracting distinctive content words.
STOPWORDS = frozenset({
    "a", "al", "algo", "ante", "antes", "aqui", "asi", "aun", "cada",
    "como", "con", "contra", "cual", "cuando", "de", "del", "desde",
    "donde", "dos", "el", "ella", "ellas", "ellos", "en", "entre", "era",
    "eran", "es", "esa", "ese", "eso", "esta", "estan", "este", "esto",
    "fue", "fueron", "ha", "hace", "han", "hasta", "hay", "la", "las",
    "le", "les", "lo", "los", "mas", "me", "menos", "mi", "mientras",
    "muy", "no", "nos", "o", "otra", "otro", "para", "pero", "por",
    "porque", "que", "quien", "se", "ser", "si", "sin", "sobre", "solo",
    "son", "su", "sus", "tan", "te", "tiene", "todo", "todos", "tu",
    "un", "una", "uno", "unos", "y", "ya",
    "a", "about", "after", "all", "also", "an", "and", "any", "are", "as",
    "at", "be", "been", "but", "by", "can", "did", "do", "for", "from",
    "had", "has", "have", "he", "her", "his", "how", "i", "if", "in",
    "into", "is", "it", "its", "just", "more", "most", "my", "no", "not",
    "of", "on", "one", "only", "or", "our", "out", "over", "she", "so",
    "some", "than", "that", "the", "their", "them", "then", "there",
    "these", "they", "this", "to", "up", "was", "we", "were", "what",
    "when", "where", "which", "who", "why", "will", "with", "you", "your",
})


def content_tokens(text: str) -> set[str]:
    """Distinctive words: 4+ chars, not stopwords, not pure digits."""
    return {
        token for token in tokens(text)
        if len(token) >= 4 and token not in STOPWORDS and not token.isdigit()
    }


def jaccard(left: set[str], right: set[str]) -> float:
    """Overlap of two token sets, 0.0 when either side is empty."""
    if not left or not right:
        return 0.0
    intersection = len(left & right)
    if not intersection:
        return 0.0
    return intersection / len(left | right)
