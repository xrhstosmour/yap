"""PostgreSQL full-text search and trigram helpers.

Builds SQLAlchemy WHERE-clause expressions for:
- Full-text search via `to_tsvector` / `plainto_tsquery`
- Trigram similarity via `pg_trgm` % operator
- Combined mode: FTS for long queries, trigram for short ones

Includes Greek language support: Greeklish-to-Greek transliteration,
automatic unaccent normalisation on search columns, and configurable
FTS language via settings.

These helpers are database-level only, no stored tsvector columns
required. Add stored tsvector columns in your own migrations for
high-volume production tables.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from sqlmodel import func
from sqlmodel import text

from app.core.greeklish import greeklish_to_greek
from app.core.settings import settings

VALID_FTS_LANGUAGES = {
    "simple",
    "english",
    "greek",
    "german",
    "french",
    "spanish",
    "italian",
    "portuguese",
    "russian",
    "turkish",
}

__all__ = [
    "SearchMode",
    "build_fts_condition",
    "build_trigram_condition",
    "build_ilike_condition",
    "fts_rank_expr",
    "choose_mode",
    "normalise_query",
]


def normalise_query(query_str: str) -> str:
    """Normalise a search query for best-match behaviour.

    Applies Greeklish-to-Greek transliteration so that typing
    `"yiannis"` matches stored `"Γιάννης"` (via unaccent on the
    column side). The query itself is not unaccented, the column
    expression is, so diacritics in the query are preserved for
    exact-match scoring.

    Args:
        query_str: Raw user-provided search query.

    Returns:
        Normalised query string.
    """
    return greeklish_to_greek(query_str.strip())


class SearchMode(StrEnum):
    """Search modes supported by the query builder helpers.

    Attributes:
        FTS: Full-text search mode.
        TRIGRAM: Trigram similarity search mode.
        COMBINED: Dispatching mode that chooses between FTS and trigram.
    """

    FTS = "fts"
    TRIGRAM = "trigram"
    COMBINED = "combined"


def build_fts_condition(
    column_expr, query_str: str, language: str | None = None
) -> Any:  # noqa: ANN401
    """Build a PostgreSQL full-text search filter expression.

    Applies `unaccent()` to the column so diacritics in stored text
    do not block matches (`"Γιάννης"` matches `"yiannis"`). The
    query is also normalised via Greeklish-to-Greek transliteration.

    Args:
        column_expr: SQLAlchemy column or SQL expression to search.
        query_str: User-provided search query.
        language: PostgreSQL text search configuration. Falls back to
            `settings.FTS_LANGUAGE` when `None`.

    Returns:
        SQLAlchemy expression equivalent to
        `to_tsvector(language, unaccent(column_expr))
        @@ plainto_tsquery(language, query_str)`.
    """
    if language is None:
        language = settings.FTS_LANGUAGE
    if language not in VALID_FTS_LANGUAGES:
        language = "simple"
    # `language` must be allowlist-checked above before interpolation: it is
    # embedded as a SQL string literal (not a bound parameter) because
    # `to_tsvector`/`plainto_tsquery` have overloads on both `regconfig` and
    # plain `text` first arguments, so an untyped bound parameter here is
    # ambiguous to PostgreSQL. This assertion is defense in depth in case the
    # allowlist check above is ever removed or bypassed.
    assert language in VALID_FTS_LANGUAGES  # noqa: S101
    query_str = normalise_query(query_str)
    language_expr: Any = text(f"'{language}'")
    return func.to_tsvector(language_expr, func.unaccent(column_expr)).op("@@")(
        func.plainto_tsquery(language_expr, query_str)
    )


def build_trigram_condition(column_expr, query_str: str, threshold: float = 0.3) -> Any:  # noqa: ANN401
    """Build a trigram similarity threshold expression.

    Applies `unaccent()` to both the column and the query so that
    diacritic differences do not block similarity matches.

    Args:
        column_expr: SQLAlchemy column or SQL expression to search.
        query_str: User-provided search query.
        threshold: Minimum similarity score in the range [0.0, 1.0].

    Returns:
        SQLAlchemy expression equivalent to
        `similarity(unaccent(column_expr), unaccent(query_str)) >= threshold`.
    """
    query_str = normalise_query(query_str)
    return (
        func.similarity(func.unaccent(column_expr), func.unaccent(query_str))
        >= threshold
    )


def build_ilike_condition(column_expr, query_str: str) -> Any:  # noqa: ANN401
    """Build an ILIKE fallback expression for non-PostgreSQL engines.

    Args:
        column_expr: SQLAlchemy column or SQL expression to search.
        query_str: User-provided search query.

    Returns:
        SQLAlchemy expression equivalent to ``column_expr ILIKE %query_str%``.
        Escapes ``%``, ``_``, and ``\\`` in the query string with a ``\\``
        escape character.
    """
    escaped = query_str.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"
    return column_expr.ilike(pattern, escape="\\")


def fts_rank_expr(column_expr, query_str: str, language: str | None = None) -> Any:  # noqa: ANN401
    """Build a PostgreSQL FTS relevance ranking expression.

    Applies `unaccent()` to the column for consistency with
    `build_fts_condition()`.

    Args:
        column_expr: SQLAlchemy column or SQL expression to rank.
        query_str: User-provided search query.
        language: PostgreSQL text search configuration. Falls back to
            `settings.FTS_LANGUAGE` when `None`.

    Returns:
        SQLAlchemy expression equivalent to
        `ts_rank(to_tsvector(language, unaccent(column_expr)),
        plainto_tsquery(language, query_str))`.
    """
    if language is None:
        language = settings.FTS_LANGUAGE
    if language not in VALID_FTS_LANGUAGES:
        language = "simple"
    # `language` must be allowlist-checked above before interpolation: it is
    # embedded as a SQL string literal (not a bound parameter) because
    # `to_tsvector`/`plainto_tsquery` have overloads on both `regconfig` and
    # plain `text` first arguments, so an untyped bound parameter here is
    # ambiguous to PostgreSQL. This assertion is defense in depth in case the
    # allowlist check above is ever removed or bypassed.
    assert language in VALID_FTS_LANGUAGES  # noqa: S101
    query_str = normalise_query(query_str)
    language_expr: Any = text(f"'{language}'")
    return func.ts_rank(
        func.to_tsvector(language_expr, func.unaccent(column_expr)),
        func.plainto_tsquery(language_expr, query_str),
    )


def choose_mode(query_str: str, min_fts_length: int = 3) -> tuple[SearchMode, str]:
    """Choose search mode based on normalized query length.

    The query is normalised before length-checking so that Greeklish
    input like `"th"` (which maps to `"θ"`) is treated correctly.

    Args:
        query_str: User-provided search query.
        min_fts_length: Minimum trimmed length required to use FTS.

    Returns:
        A tuple of ``(SearchMode, normalized_query)``. Callers should pass the
        pre-normalized query to ``build_fts_condition`` / ``build_trigram_condition``
        to avoid a second normalization pass.
    """
    normalized = normalise_query(query_str)
    if len(normalized) < min_fts_length:
        return SearchMode.TRIGRAM, normalized
    return SearchMode.FTS, normalized
