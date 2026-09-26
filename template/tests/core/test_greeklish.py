"""Tests for Greeklish-to-Greek and Greek-to-Greeklish transliteration."""

from __future__ import annotations

import pytest

from app.core.greeklish import GREEK_TO_GREEKLISH
from app.core.greeklish import GREEKLISH_TO_GREEK
from app.core.greeklish import greek_to_greeklish
from app.core.greeklish import greeklish_to_greek


class TestGreeklishToGreek:
    """Tests for greeklish_to_greek()."""

    def test_basic_transliteration(self) -> None:
        """Common Greeklish words should convert to approximate Greek."""
        result = greeklish_to_greek("thessaloniki")
        # "th" -> θ, "e" -> ε, "s" -> σ, "s" -> σ, "a" -> α,
        # "l" -> λ, "o" -> ο, "n" -> ν, "i" -> ι, "k" -> κ, "i" -> ι
        assert result == "θεσσαλονικι"

    def test_mixed_text_preserves_unmatched(self) -> None:
        """Non-matching characters (digits, punctuation) should pass through unchanged."""
        result = greeklish_to_greek("hello world!")
        # "h" -> η, "e" -> ε, "l" -> λ, "l" -> λ, "o" -> ο,
        # " " stays, "w" -> ω, "o" -> ο, "r" -> ρ, "l" -> λ, "d" -> δ, "!" stays
        assert result == "ηελλο ωορλδ!"

    def test_empty_string(self) -> None:
        """Empty input should return empty string."""
        assert greeklish_to_greek("") == ""

    def test_already_greek_stays_as_is(self) -> None:
        """Already Greek characters should be left unchanged by the regex."""
        result = greeklish_to_greek("καλημερα")
        # None of the Greek characters match the Greeklish patterns, so they pass through.
        assert result == "καλημερα"

    def test_already_latin_stays_as_is_when_no_match(self) -> None:
        """Latin characters without Greeklish mappings pass through."""
        result = greeklish_to_greek("abc")
        # "a" -> α, "b" -> μπ, "c" is not in the map and passes through.
        assert result == "αμπc"

    def test_multi_char_pattern_before_single(self) -> None:
        """Multi-character patterns like 'th' should match before single 't' then 'h'."""
        result = greeklish_to_greek("th")
        # "th" should be mapped as a whole to θ, not "t"→τ + "h"→η
        assert result == "θ"

    def test_case_insensitive(self) -> None:
        """Input should be treated case-insensitively."""
        result = greeklish_to_greek("Thessaloniki")
        assert result == "θεσσαλονικι"


class TestGreekToGreeklish:
    """Tests for greek_to_greeklish()."""

    def test_basic_transliteration(self) -> None:
        """Common Greek text should produce Greeklish candidates."""
        # "θ" has ["th", "8"] in GREEK_TO_GREEKLISH
        candidates = greek_to_greeklish("θεσσαλονικη")
        # Many candidates due to multiple expansions per character,
        # but "th" variant should be among them.
        assert len(candidates) > 0
        assert any("thessaloniki" in c for c in candidates)

    def test_empty_string(self) -> None:
        """Empty input should return a list with an empty string."""
        candidates = greek_to_greeklish("")
        assert candidates == [""]

    def test_already_latin_passthrough(self) -> None:
        """Latin characters without Greek mappings should pass through."""
        result = greek_to_greeklish("abc")
        assert result == ["abc"]

    def test_respects_max_expansions(self) -> None:
        """Output should not exceed max_expansions."""
        # A long Greek string with many possible expansions
        candidates = greek_to_greeklish("ανθρωπος", max_expansions=5)
        assert len(candidates) <= 5


class TestGreeklishRoundTrip:
    """The two maps describe one correspondence and must agree."""

    @pytest.mark.parametrize(
        ("greeklish", "greek"),
        [
            ("taverna", "ταβερνα"),
            ("souvlaki", "σουβλακι"),
            ("bifteki", "μπιφτεκι"),
            ("mpifteki", "μπιφτεκι"),
            ("ouzeri", "ουζερι"),
            ("kafeteria", "καφετερια"),
            ("tzatziki", "τζατζικι"),
            ("ntolmades", "ντολμαδες"),
            ("psarotaverna", "ψαροταβερνα"),
            ("saganaki", "σαγανακι"),
        ],
    )
    def test_common_words_transliterate_exactly(
        self, greeklish: str, greek: str
    ) -> None:
        """Everyday words map to their accentless Greek spelling.

        `v` and `b` were previously the failure: `v` was absent from the
        map so "taverna" kept a Latin `v`, and `b` produced β rather than
        μπ, so neither word could match anything stored in Greek.
        """
        assert greeklish_to_greek(greeklish) == greek

    @pytest.mark.parametrize(
        "greek", ["ταβερνα", "σουβλακι", "μπιφτεκι", "γυρος", "ουζερι", "φετα"]
    )
    def test_first_greeklish_candidate_maps_back(self, greek: str) -> None:
        """The preferred Greeklish spelling transliterates back to the original."""
        preferred = greek_to_greeklish(greek)[0]
        assert greeklish_to_greek(preferred) == greek

    def test_word_final_sigma_uses_terminal_form(self) -> None:
        """A trailing sigma becomes ς, which is how Greek text is stored."""
        assert greeklish_to_greek("gyros") == "γυρος"
        assert greeklish_to_greek("gyros souvlaki") == "γυρος σουβλακι"

    def test_medial_sigma_is_unchanged(self) -> None:
        """Only a word-final sigma is rewritten."""
        assert greeklish_to_greek("kosta") == "κοστα"

    def test_every_greeklish_expansion_is_mappable_back(self) -> None:
        """No expansion may exist that the reverse map cannot consume.

        A spelling offered by one table and unknown to the other is the
        defect this pair of maps keeps reintroducing.
        """
        known = set(GREEKLISH_TO_GREEK)
        unmappable = {
            expansion
            for expansions in GREEK_TO_GREEKLISH.values()
            for expansion in expansions
            if expansion.isascii() and expansion not in known
        }
        # `af`/`av`/`ef`/`ev` are intentionally one-way, see the comment on
        # `GREEKLISH_TO_GREEK`: mapping them back would rewrite ordinary
        # letter pairs such as the "av" inside "taverna".
        assert unmappable <= {"af", "av", "ef", "ev"}
