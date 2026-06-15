"""Greeklish-to-Greek and Greek-to-Greeklish transliteration utilities.

Provides bidirectional conversion between Greek characters and common
Latin-based Greeklish (Greek written with the Latin alphabet).

Can be used independently for search normalisation, data transformation,
or any other Greek/Greeklish processing need.
"""

from __future__ import annotations

import re
from typing import Final

GREEKLISH_TO_GREEK: Final[dict[str, str]] = {
    "a": "\u03b1", "b": "\u03b2", "g": "\u03b3", "d": "\u03b4", "e": "\u03b5",
    "z": "\u03b6", "h": "\u03b7", "8": "\u03b8", "i": "\u03b9", "k": "\u03ba",
    "l": "\u03bb", "m": "\u03bc", "n": "\u03bd", "x": "\u03be", "o": "\u03bf",
    "p": "\u03c0", "r": "\u03c1", "s": "\u03c3", "t": "\u03c4", "y": "\u03c5",
    "f": "\u03c6", "ch": "\u03c7", "ps": "\u03c8", "w": "\u03c9",
    "th": "\u03b8", "ei": "\u03b5\u03b9", "oi": "\u03bf\u03b9",
    "ui": "\u03c5\u03b9", "ou": "\u03bf\u03c5", "au": "\u03b1\u03c5",
    "eu": "\u03b5\u03c5",
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
    key=len, reverse=True,
)

TONOS_MAP: Final = str.maketrans(
    "\u0386\u0388\u0389\u038a\u038c\u038e\u038f"
    "\u03ac\u03ad\u03ae\u03af\u03cc\u03ce\u03cd\u03cb\u03ca\u03b0",
    "\u0391\u0395\u0397\u0399\u039f\u03a5\u03a9"
    "\u03b1\u03b5\u03b7\u03b9\u03bf\u03c5\u03c5\u03b9\u03b9\u03c5",
)


def remove_tonos(text: str) -> str:
    """Remove Greek tonos (accent) characters from text."""
    return text.translate(TONOS_MAP)


def greeklish_to_greek(text: str) -> str:
    """Transliterate Greeklish text to Greek characters.

    Examples::

        greeklish_to_greek("kalimera") -> "kalimera"
        greeklish_to_greek("yiannis")  -> "yiannis"

    Multi-character patterns like ``th`` are matched before single
    characters to avoid partial replacement.

    Args:
        text: Greeklish text (Latin characters).

    Returns:
        Approximate Greek transliteration. Non-matching characters are
        left unchanged so that mixed text and punctuation are preserved.
    """
    def _replace(match: re.Match) -> str:
        return GREEKLISH_TO_GREEK[match.group(0).lower()]

    return GREEKLISH_RE.sub(_replace, text.lower())  # type: ignore[no-any-return]


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
                candidates = [
                    c + e for c in candidates for e in expansions
                ][:max_expansions]
                i += len(pattern)
                matched = True
                break
        if matched:
            continue
        ch = text[i]
        expansions = GREEK_TO_GREEKLISH.get(ch, [ch])
        candidates = [
            c + e for c in candidates for e in expansions
        ][:max_expansions]
        i += 1
    return candidates
