"""Unit tests for search expression helpers."""

from __future__ import annotations

from sqlalchemy import column

from app.core.search import SearchMode
from app.core.search import build_fts_condition
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
        mode, _ = choose_mode("ab")
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
