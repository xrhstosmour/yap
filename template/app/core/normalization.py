"""Normalisation for the columns Greek search matches against.

Search has to work for someone typing Greek with accents, Greek without
accents, or Greeklish on a Latin keyboard. Rather than three indexes, every
searchable name is stored once in one normalised form, and the query is put
through the same funnel before it is compared.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final

from app.core.greeklish import greeklish_to_greek

# Anything that is not a letter, a digit or a single space. Punctuation in a
# venue name ("Ο Θανάσης", "Κρητικός - Ψητοπωλείο") is noise for matching.
_NON_SEARCHABLE_RE: Final[re.Pattern] = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE_RE: Final[re.Pattern] = re.compile(r"\s+")

# A string is treated as Greeklish only if it carries no Greek letters at
# all. Mixed input is far more likely to be Greek with a stray Latin letter
# than a genuine transliteration.
_GREEK_LETTER_RE: Final[re.Pattern] = re.compile(r"[Ͱ-Ͽἀ-῿]")


def normalize_name(text: str) -> str:
    """Fold a name into the form stored in a ``name_normalized`` column.

    Lowercases, strips accents and punctuation, and collapses whitespace.
    Greek stays Greek. Use this for what goes into the database.
    """
    # Decompose, drop every combining mark, recompose. This strips Greek
    # tonos and diaeresis and Latin diacritics in one pass, so a venue
    # written "Café" is still found by someone typing "cafe". Doing it with
    # `remove_tonos` alone would fold the Greek and leave the Latin.
    decomposed = unicodedata.normalize("NFD", text.strip().lower())
    folded = "".join(
        character for character in decomposed if unicodedata.category(character) != "Mn"
    )
    folded = unicodedata.normalize("NFC", folded)
    folded = _NON_SEARCHABLE_RE.sub(" ", folded)
    return _WHITESPACE_RE.sub(" ", folded).strip()


def normalize_query(text: str) -> str:
    """Fold a search term into the same form, transliterating Greeklish.

    Latin-only input is assumed to be Greeklish and is transliterated
    before folding, so "bifteki" reaches the same string as "μπιφτέκι".
    The transliteration is approximate by design, see
    :func:`app.core.greeklish.greeklish_to_greek`, which is why the columns
    it is compared against carry trigram indexes rather than being matched
    for equality.
    """
    folded = normalize_name(text)
    if folded and not _GREEK_LETTER_RE.search(folded):
        folded = normalize_name(greeklish_to_greek(folded))
    return folded
