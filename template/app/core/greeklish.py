"""Greeklish-to-Greek and Greek-to-Greeklish transliteration utilities.

Provides bidirectional conversion between Greek characters and common
Latin-based Greeklish (Greek written with the Latin alphabet).

Can be used independently for search normalisation, data transformation,
or any other Greek/Greeklish processing need.
"""

from __future__ import annotations

import re
from typing import Final

# Keep this in step with `GREEK_TO_GREEKLISH` below. The two tables describe
# the same correspondence from opposite ends, and a letter present in one and
# missing from the other breaks the round trip: `greek_to_greeklish` would
# offer a spelling that `greeklish_to_greek` then fails to map back.
#
# Where a Greeklish sequence is genuinely ambiguous the most common modern
# convention wins, since only one Greek form can be produced:
#   - `b` is \u03bc\u03c0 and `v` is \u03b2, the usual split, so "bira" reads
#     \u03bc\u03c0\u03b9\u03c1\u03b1 and "taverna" reads
#     \u03c4\u03b1\u03b2\u03ad\u03c1\u03bd\u03b1.
#   - `x` stays \u03be. `ch` already covers \u03c7.
#
# `af`/`av`/`ef`/`ev` are deliberately absent even though `GREEK_TO_GREEKLISH`
# emits them. Whether those letter pairs represent \u03b1\u03c5/\u03b5\u03c5
# depends on the word, not on the letters, so mapping them here would rewrite
# ordinary pairs: it turned "taverna" into \u03c4\u03b1\u03c5\u03b5\u03c1\u03bd\u03b1
# and "kafeteria" into \u03ba\u03b1\u03c5\u03b5\u03c4\u03b5\u03c1\u03b9\u03b1.
# The plain `a`+`f` path already produces a form close enough for trigram
# matching.
GREEKLISH_TO_GREEK: Final[dict[str, str]] = {
    "a": "\u03b1",
    "v": "\u03b2",
    "g": "\u03b3",
    "d": "\u03b4",
    "e": "\u03b5",
    "z": "\u03b6",
    "h": "\u03b7",
    "8": "\u03b8",
    "i": "\u03b9",
    "k": "\u03ba",
    "l": "\u03bb",
    "m": "\u03bc",
    "n": "\u03bd",
    "x": "\u03be",
    "o": "\u03bf",
    "p": "\u03c0",
    "r": "\u03c1",
    "s": "\u03c3",
    "t": "\u03c4",
    "y": "\u03c5",
    "u": "\u03c5",
    "f": "\u03c6",
    "w": "\u03c9",
    "b": "\u03bc\u03c0",
    "ch": "\u03c7",
    "ps": "\u03c8",
    "th": "\u03b8",
    "ph": "\u03c6",
    "ks": "\u03be",
    "ai": "\u03b1\u03b9",
    "ei": "\u03b5\u03b9",
    "oi": "\u03bf\u03b9",
    "ui": "\u03c5\u03b9",
    "ou": "\u03bf\u03c5",
    "au": "\u03b1\u03c5",
    "eu": "\u03b5\u03c5",
    "mp": "\u03bc\u03c0",
    "nt": "\u03bd\u03c4",
    "gk": "\u03b3\u03ba",
    "ng": "\u03b3\u03b3",
    "ts": "\u03c4\u03c3",
    "tz": "\u03c4\u03b6",
    "yi": "\u03b3\u03b9",
    "gi": "\u03b3\u03b9",
}

GREEKLISH_PATTERNS: Final[list[str]] = sorted(
    GREEKLISH_TO_GREEK.keys(), key=len, reverse=True
)

GREEKLISH_RE: Final[re.Pattern] = re.compile(
    "|".join(re.escape(p) for p in GREEKLISH_PATTERNS),
    re.IGNORECASE,
)

GREEK_TO_GREEKLISH: Final[dict[str, list[str]]] = {
    "\u03b1": ["a"],
    "\u03b2": ["v", "b"],
    "\u03b3": ["g"],
    "\u03b4": ["d"],
    "\u03b5": ["e"],
    "\u03b6": ["z"],
    "\u03b7": ["i", "h"],
    "\u03b8": ["th", "8"],
    "\u03b9": ["i"],
    "\u03ba": ["k"],
    "\u03bb": ["l"],
    "\u03bc": ["m"],
    "\u03bd": ["n"],
    "\u03be": ["x", "ks"],
    "\u03bf": ["o"],
    "\u03c0": ["p"],
    "\u03c1": ["r"],
    "\u03c3": ["s"],
    "\u03c2": ["s"],
    "\u03c4": ["t"],
    "\u03c5": ["y", "u", "i"],
    "\u03c6": ["f", "ph"],
    "\u03c7": ["ch", "x", "h"],
    "\u03c8": ["ps"],
    "\u03c9": ["o", "w"],
    "\u03b1\u03b9": ["ai", "e"],
    "\u03b5\u03b9": ["ei", "i"],
    "\u03bf\u03b9": ["oi", "i"],
    "\u03bf\u03c5": ["ou", "u"],
    "\u03b5\u03c5": ["eu", "ef", "ev"],
    "\u03b1\u03c5": ["au", "af", "av"],
    "\u03bc\u03c0": ["b", "mp"],
    "\u03b3\u03b3": ["ng", "g"],
    "\u03b3\u03ba": ["gk", "g"],
    "\u03bd\u03c4": ["nt", "d"],
}

GREEK_DIGRAPHS: Final[list[str]] = sorted(
    [k for k in GREEK_TO_GREEKLISH if len(k) > 1],
    key=len,
    reverse=True,
)

TONOS_MAP: Final = str.maketrans(
    "\u0386\u0388\u0389\u038a\u038c\u038e\u038f"
    "\u03ac\u03ad\u03ae\u03af\u03cc\u03ce\u03cd\u03cb\u03ca\u03b0",
    "\u0391\u0395\u0397\u0399\u039f\u03a5\u03a9"
    "\u03b1\u03b5\u03b7\u03b9\u03bf\u03c5\u03c5\u03b9\u03b9\u03c5",
)


_FINAL_SIGMA_RE: Final[re.Pattern] = re.compile(r"\u03c3\b")


def remove_tonos(text: str) -> str:
    """Remove Greek tonos (accent) characters from text."""
    return text.translate(TONOS_MAP)


def greeklish_to_greek(text: str) -> str:
    """Transliterate Greeklish text to Greek characters.

    Examples::

        greeklish_to_greek("taverna")  -> "\u03c4\u03b1\u03b2\u03b5\u03c1\u03bd\u03b1"
        greeklish_to_greek("bifteki")  -> "\u03bc\u03c0\u03b9\u03c6\u03c4\u03b5\u03ba\u03b9"
        greeklish_to_greek("gyros")    -> "\u03b3\u03c5\u03c1\u03bf\u03c2"

    Multi-character patterns like ``th`` are matched before single
    characters to avoid partial replacement.

    Args:
        text: Greeklish text (Latin characters).

    Returns:
        Approximate Greek transliteration, accentless and with a
        word-final sigma. Non-matching characters are left unchanged so
        that mixed text and punctuation are preserved.

    Note:
        The result is approximate by construction. Greeklish is
        many-to-many, so vowels stay ambiguous: `i` could be
        \u03b9, \u03b7 or \u03c5, and `o` could be \u03bf or \u03c9.
        "kritikos" therefore yields \u03ba\u03c1\u03b9\u03c4\u03b9\u03ba\u03bf\u03c2
        rather than \u03ba\u03c1\u03b7\u03c4\u03b9\u03ba\u03cc\u03c2. Callers are
        expected to feed the output to trigram or full-text matching, which
        absorbs a wrong vowel, not to compare it for equality.
    """

    def _replace(match: re.Match) -> str:
        return GREEKLISH_TO_GREEK[match.group(0).lower()]

    transliterated = GREEKLISH_RE.sub(_replace, text.lower())
    return _apply_final_sigma(transliterated)


def _apply_final_sigma(text: str) -> str:
    """Rewrite a word-final sigma to its terminal form.

    Greek writes sigma as \u03c2 at the end of a word and \u03c3 everywhere
    else, but Greeklish has only one `s`. Without this, "gyros" transliterates
    to \u03b3\u03c5\u03c1\u03bf\u03c3 and never matches a stored
    \u03b3\u03cd\u03c1\u03bf\u03c2, because `unaccent` strips diacritics
    and does not fold the two sigmas together.
    """
    return _FINAL_SIGMA_RE.sub("\u03c2", text)


def greek_to_greeklish(text: str, max_expansions: int = 10) -> list[str]:
    """Transliterate Greek text to one or more Greeklish forms.

    Produces plausible Latin expansions for each Greek letter or
    digraph. The result is a list of candidate Greeklish strings,
    limited to ``max_expansions`` entries.

    Args:
        text: Greek text (
            e.g. ``"\\u03b1\\u03bd\\u03b8\\u03c1\\u03c9\\u03c0\\u03bf\\u03c2"``
        ).
        max_expansions: Maximum number of Greeklish candidates to return.

    Returns:
        List of Greeklish candidates (most common first).
    """
    text = remove_tonos(text)
    candidates: list[str] = [""]
    i = 0
    while i < len(text):
        matched = False
        for pattern in GREEK_DIGRAPHS:
            if text[i:].startswith(pattern):
                expansions = GREEK_TO_GREEKLISH[pattern]
                candidates = [c + e for c in candidates for e in expansions][
                    :max_expansions
                ]
                i += len(pattern)
                matched = True
                break
        if matched:
            continue
        ch = text[i]
        expansions = GREEK_TO_GREEKLISH.get(ch, [ch])
        candidates = [c + e for c in candidates for e in expansions][:max_expansions]
        i += 1
    return candidates
