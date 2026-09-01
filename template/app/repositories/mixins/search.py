# mypy: disable-error-code="attr-defined"
"""SearchMixin for repository layer.

Adds search_fts(), search_trigram(), and search_combined() methods
to any BaseRepository subclass. Uses app.core.search helpers.
"""

from __future__ import annotations

from typing import Any

from sqlmodel import and_
from sqlmodel import func
from sqlmodel import or_
from sqlmodel import select

from app.core.search import SearchMode
from app.core.search import build_fts_condition
from app.core.search import build_trigram_condition
from app.core.search import choose_mode
from app.core.search import fts_rank_expr


class SearchMixin:
    """Reusable repository mixin for multi-field search operations.

    This mixin expects subclasses to provide BaseRepository-compatible
    attributes and helpers, including ``self.model``, ``self.session``,
    ``self._apply_tenant_filter()``, and ``self._apply_soft_delete_filter()``.
    """

    def _apply_search_filters(self, query, filters: dict[str, Any] | None):  # noqa: ANN001, ANN202
        """Apply equality filters to a search query.

        Mirrors `BaseRepository.list`: only real model attributes are
        considered, and a `None` value means "not filtering on this",
        not "match NULL".

        Args:
            query: The select to narrow.
            filters: Field/value pairs to require.

        Returns:
            The query, narrowed if there was anything to narrow by.
        """
        if not filters:
            return query

        conditions = [
            getattr(self.model, field) == value
            for field, value in filters.items()
            if value is not None and hasattr(self.model, field)
        ]
        return query.where(and_(*conditions)) if conditions else query

    async def search_fts(
        self,
        query_str: str,
        fields: list[str],
        language: str | None = None,
        skip: int = 0,
        limit: int = 20,
        include_deleted: bool = False,
        filters: dict[str, Any] | None = None,
    ) -> tuple[list[Any], int]:
        """Search records with PostgreSQL full-text search conditions.

        Args:
            query_str: User-provided search query.
            fields: Model attribute names to search across.
            language: PostgreSQL text search configuration.
            skip: Number of matching rows to skip.
            limit: Maximum number of rows to return.
            include_deleted: Whether to include soft-deleted rows.
            filters: Field/value pairs to require alongside the search.

        Returns:
            Tuple containing matched records and total count.

        Raises:
            ValueError: If no searchable fields are provided.
        """
        if not fields:
            raise ValueError("fields must contain at least one model attribute")

        conditions = [
            build_fts_condition(getattr(self.model, field), query_str, language)
            for field in fields
            if hasattr(self.model, field)
        ]
        if not conditions:
            raise ValueError("No valid searchable fields found on model")

        search_filter = or_(*conditions)

        rank_values = [
            fts_rank_expr(getattr(self.model, field), query_str, language)
            for field in fields
            if hasattr(self.model, field)
        ]
        rank_expr = sum(rank_values[1:], rank_values[0])

        query = select(self.model).where(search_filter).order_by(rank_expr.desc())
        count_query = select(func.count()).select_from(self.model).where(search_filter)

        query = self._apply_tenant_filter(query)
        count_query = self._apply_tenant_filter(count_query)
        query = self._apply_soft_delete_filter(query, include_deleted)
        count_query = self._apply_soft_delete_filter(count_query, include_deleted)
        query = self._apply_search_filters(query, filters)
        count_query = self._apply_search_filters(count_query, filters)

        count_result = await self.session.execute(count_query)
        total = count_result.scalar() or 0

        query = query.offset(skip).limit(limit)
        result = await self.session.execute(query)
        records = result.scalars().all()

        return list(records), total

    async def search_trigram(
        self,
        query_str: str,
        fields: list[str],
        threshold: float = 0.3,
        skip: int = 0,
        limit: int = 20,
        include_deleted: bool = False,
        filters: dict[str, Any] | None = None,
    ) -> tuple[list[Any], int]:
        """Search records with trigram similarity conditions.

        Args:
            query_str: User-provided search query.
            fields: Model attribute names to search across.
            threshold: Minimum trigram similarity threshold.
            skip: Number of matching rows to skip.
            limit: Maximum number of rows to return.
            include_deleted: Whether to include soft-deleted rows.
            filters: Field/value pairs to require alongside the search.

        Returns:
            Tuple containing matched records and total count.

        Raises:
            ValueError: If no searchable fields are provided.
        """
        if not fields:
            raise ValueError("fields must contain at least one model attribute")

        conditions = [
            build_trigram_condition(getattr(self.model, field), query_str, threshold)
            for field in fields
            if hasattr(self.model, field)
        ]
        if not conditions:
            raise ValueError("No valid searchable fields found on model")

        search_filter = or_(*conditions)

        query = select(self.model).where(search_filter)
        count_query = select(func.count()).select_from(self.model).where(search_filter)

        query = self._apply_tenant_filter(query)
        count_query = self._apply_tenant_filter(count_query)
        query = self._apply_soft_delete_filter(query, include_deleted)
        count_query = self._apply_soft_delete_filter(count_query, include_deleted)
        query = self._apply_search_filters(query, filters)
        count_query = self._apply_search_filters(count_query, filters)

        count_result = await self.session.execute(count_query)
        total = count_result.scalar() or 0

        query = query.offset(skip).limit(limit)
        result = await self.session.execute(query)
        records = result.scalars().all()

        return list(records), total

    async def search_combined(
        self,
        query_str: str,
        fields: list[str],
        language: str | None = None,
        trigram_threshold: float = 0.3,
        min_fts_length: int = 3,
        skip: int = 0,
        limit: int = 20,
        include_deleted: bool = False,
        filters: dict[str, Any] | None = None,
    ) -> tuple[list[Any], int]:
        """Dispatch search to FTS or trigram based on query length.

        Args:
            query_str: User-provided search query.
            fields: Model attribute names to search across.
            language: PostgreSQL text search configuration for FTS.
            trigram_threshold: Minimum trigram similarity threshold.
            min_fts_length: Minimum trimmed length required to use FTS.
            skip: Number of matching rows to skip.
            limit: Maximum number of rows to return.
            include_deleted: Whether to include soft-deleted rows.
            filters: Field/value pairs to require alongside the search.

        Returns:
            Tuple containing matched records and total count.
        """
        mode, normalized = choose_mode(query_str, min_fts_length=min_fts_length)
        if mode == SearchMode.TRIGRAM:
            return await self.search_trigram(
                query_str=normalized,
                fields=fields,
                threshold=trigram_threshold,
                skip=skip,
                limit=limit,
                include_deleted=include_deleted,
                filters=filters,
            )

        return await self.search_fts(
            query_str=normalized,
            fields=fields,
            language=language,
            skip=skip,
            limit=limit,
            include_deleted=include_deleted,
            filters=filters,
        )
