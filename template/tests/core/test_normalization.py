"""Tests for the name and query normalisation used by Greek search."""

from __future__ import annotations

import pytest

from app.core.normalization import normalize_name
from app.core.normalization import normalize_query


class TestNormalizeName:
    """Tests for normalize_name(), which folds what goes into the database."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Ο Θανάσης", "ο θανασης"),
            ("ΜΠΙΦΤΕΚΙ", "μπιφτεκι"),
            ("Κρητικός - Ψητοπωλείο", "κρητικος ψητοπωλειο"),
            ("  Γύρος   Πίτα  ", "γυρος πιτα"),
            ("Café Nero", "cafe nero"),
        ],
    )
    def test_folds_case_accents_and_punctuation(self, raw: str, expected: str) -> None:
        """Names lose case, accents and punctuation, and collapse whitespace."""
        assert normalize_name(raw) == expected

    def test_greek_stays_greek(self) -> None:
        """A Greek name is never transliterated on the way in."""
        assert normalize_name("Μπιφτέκι") == "μπιφτεκι"

    def test_empty_string(self) -> None:
        """An empty name normalises to an empty string rather than raising."""
        assert normalize_name("") == ""

    def test_punctuation_only(self) -> None:
        """A name of pure punctuation folds away to nothing."""
        assert normalize_name("--- !!! ---") == ""

    def test_is_idempotent(self) -> None:
        """Normalising an already normalised name changes nothing.

        This matters because the stored column is compared against the
        output of `normalize_query`, which calls this function again.
        """
        once = normalize_name("Ο Θανάσης")
        assert normalize_name(once) == once


class TestNormalizeQuery:
    """Tests for normalize_query(), which folds what the user types."""

    @pytest.mark.parametrize(
        ("typed", "expected"),
        [
            ("bifteki", "μπιφτεκι"),
            ("Μπιφτέκι", "μπιφτεκι"),
            ("gyros", "γυρος"),
            ("souvlaki", "σουβλακι"),
        ],
    )
    def test_greek_and_greeklish_reach_the_same_string(
        self, typed: str, expected: str
    ) -> None:
        """Both keyboards converge on one comparable form."""
        assert normalize_query(typed) == expected

    def test_latin_only_input_is_transliterated(self) -> None:
        """Input with no Greek letter is treated as Greeklish."""
        assert normalize_query("taverna") == "ταβερνα"

    def test_mixed_script_is_left_as_greek(self) -> None:
        """A single Latin letter among Greek does not make it Greeklish.

        Mixed input is far more likely to be Greek with a stray keystroke
        than a genuine transliteration, and transliterating it would
        mangle the Greek that is already correct.
        """
        assert normalize_query("μπιφτεκι s") == "μπιφτεκι s"

    def test_empty_string(self) -> None:
        """An empty query normalises to an empty string rather than raising."""
        assert normalize_query("") == ""

    def test_word_final_sigma(self) -> None:
        """Greeklish `s` at the end of a word becomes a terminal sigma.

        Without it the transliteration ends in σ and never matches a
        stored ς, because accent folding does not fold the two sigmas
        together.
        """
        assert normalize_query("gyros").endswith("ς")
