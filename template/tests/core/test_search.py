"""Unit tests for search expression helpers."""

from __future__ import annotations

from sqlalchemy import column

from app.core.search import SearchMode
from app.core.search import build_fts_condition
from app.core.search import build_ilike_condition
from app.core.search import build_trigram_condition
from app.core.search import choose_mode
from app.core.search import fts_rank_expr


class TestBuildFtsCondition:
    """Tests for build_fts_condition()."""

    def test_produces_tsvector_expression(self) -> None:
        """Ensure the expression uses to_tsvector.

        Returns:
            None.
        """
        expr = build_fts_condition(column("email"), "alice")
        assert "to_tsvector" in str(expr)

    def test_uses_language_param(self) -> None:
        """Ensure the configured language appears in SQL.

        Returns:
            None.
        """
        expr = build_fts_condition(column("email"), "alice", language="english")
        assert "english" in str(expr)

    def test_uses_plainto_tsquery(self) -> None:
        """Ensure the expression uses plainto_tsquery.

        Returns:
            None.
        """
        expr = build_fts_condition(column("email"), "alice")
        assert "plainto_tsquery" in str(expr)


class TestBuildTrigramCondition:
    """Tests for build_trigram_condition()."""

    def test_uses_similarity_function(self) -> None:
        """Ensure the expression calls similarity().

        Returns:
            None.
        """
        expr = build_trigram_condition(column("full_name"), "ali")
        assert "similarity" in str(expr)

    def test_threshold_appears_in_expression(self) -> None:
        """Ensure the threshold value is represented in SQL.

        Returns:
            None.
        """
        expr = build_trigram_condition(column("full_name"), "ali", threshold=0.42)
        expr_str = str(expr)
        assert "0.42" in expr_str or ":similarity" in expr_str


class TestChooseMode:
    """Tests for choose_mode()."""

    def test_short_query_returns_trigram(self) -> None:
        """Ensure short queries use trigram mode.

        Returns:
            None.
        """
        # "ai" normalises to the two-character αι. "ab" is no longer a valid
        # case here: `b` maps to the digraph μπ, so it normalises to three
        # characters and legitimately reaches the FTS threshold.
        mode, _ = choose_mode("ai")
        assert mode == SearchMode.TRIGRAM

    def test_long_query_returns_fts(self) -> None:
        """Ensure long queries use FTS mode.

        Returns:
            None.
        """
        mode, _ = choose_mode("abcd")
        assert mode == SearchMode.FTS

    def test_exact_boundary(self) -> None:
        """Ensure boundary length selects FTS mode.

        Returns:
            None.
        """
        mode, _ = choose_mode("abc", min_fts_length=3)
        assert mode == SearchMode.FTS


class TestFtsRankExpr:
    """Tests for fts_rank_expr()."""

    def test_produces_ts_rank(self) -> None:
        """Ensure the expression uses ts_rank.

        Returns:
            None.
        """
        expr = fts_rank_expr(column("full_name"), "alice")
        assert "ts_rank" in str(expr)


class TestIlikeFallbackNormalisation:
    """The fallback path treats a query the same way the PostgreSQL paths do."""

    def test_greeklish_is_transliterated_before_matching(self) -> None:
        """Greeklish input reaches the pattern as Greek, not as Latin."""
        condition = build_ilike_condition(column("name"), "taverna")
        pattern = next(iter(condition.compile().params.values()))
        assert "ταβερνα" in pattern

    def test_wildcards_are_still_escaped_after_normalisation(self) -> None:
        """Normalising first must not reintroduce unescaped LIKE wildcards."""
        condition = build_ilike_condition(column("name"), "100%_a")
        pattern = next(iter(condition.compile().params.values()))
        assert "\\%" in pattern
        assert "\\_" in pattern
