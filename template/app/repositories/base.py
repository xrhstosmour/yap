"""Base repository with multi-tenant support and soft delete.

This module provides a generic repository class with common CRUD
operations, automatic tenant filtering, and soft delete support.
"""

from __future__ import annotations

import base64
from datetime import UTC
from datetime import date as date_type
from datetime import datetime
from datetime import time as time_type
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING
from typing import Any
from typing import Protocol
from typing import TypeVar
from typing import cast
from uuid import UUID

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapper
from sqlmodel import SQLModel
from sqlmodel import and_
from sqlmodel import func
from sqlmodel import select

from app.core import SYSTEM_TENANT_ID
from app.core.logging import get_logger
from app.core.pagination import MAX_PAGE_SIZE
from app.core.tenant import get_current_tenant_id
from app.core.tenant import is_system_access

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = get_logger("repository")

T = TypeVar("T", bound=SQLModel)


class TenantContextRequiredError(Exception):
    """Raised when a tenant-scoped query runs with no tenant context and no
    explicit `system_context()` opt-in.

    See `BaseRepository._apply_tenant_filter()`.
    """


def _jsonable(value: Any, _depth: int = 0) -> Any:  # noqa: ANN401
    """Convert model values to JSON-serializable primitives."""
    if _depth > 10:
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date_type, time_type)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return base64.b64encode(value).decode()
    if isinstance(value, dict):
        return {key: _jsonable(val, _depth + 1) for key, val in value.items()}
    if isinstance(value, list):
        return [_jsonable(item, _depth + 1) for item in value]
    return value


class ModelColumns(Protocol):
    id: Any
    created_at: Any
    __tablename__: str


class BaseRepository[T: SQLModel]:
    """Generic repository with CRUD operations and tenant support.

    Provides a base class for all repositories with common operations:
    - Create, read, update, delete
    - Automatic tenant filtering
    - Soft delete support
    - Pagination and filtering

    Type Parameters:
        T: SQLModel subclass for the entity

    Example:
        class UserRepository(BaseRepository[User]):
            def __init__(self, session: AsyncSession):
                super().__init__(session, User)
    """

    def __init__(self, session: AsyncSession, model: type[T]) -> None:
        """Initialize repository with session and model.

        Args:
            session: Async SQLAlchemy session
            model: SQLModel class for this repository
        """
        self.session = session
        self.model = model

    @property
    def _model_with_columns(self) -> ModelColumns:
        return cast(ModelColumns, self.model)

    def _apply_tenant_filter(self, query) -> Any:  # noqa: ANN401
        """Apply tenant filter to query if model has tenant_id.

        For multi-tenant models, automatically filters to only return
        records belonging to the current tenant. If the model is
        tenant-scoped but no tenant context is set, raises rather than
        running the query unfiltered, unless `system_context()` is active:
        an accidental missing `tenant_context(...)` and a deliberate
        cross-tenant sweep used to look identical (both ran unfiltered),
        which meant a forgotten tenant context silently leaked every
        tenant's rows instead of failing loudly.

        Args:
            query: SQLAlchemy query to modify

        Returns:
            Query with tenant filter applied

        Raises:
            TenantContextRequiredError: If the model is tenant-scoped, no
                tenant context is set, and `system_context()` is not active.
        """
        mapper = inspect(self.model)
        is_tenant_scoped = mapper is not None and "tenant_id" in {
            col.key for col in mapper.columns
        }
        if not is_tenant_scoped:
            return query

        tenant_id = get_current_tenant_id()
        if tenant_id is not None:
            model_tenant_id = getattr(self.model, "tenant_id", None)
            if model_tenant_id is not None:
                return query.where(model_tenant_id == tenant_id)
            return query

        if is_system_access():
            return query

        message = (
            f"{self.model.__name__} is tenant-scoped, but no tenant context "
            "is set. Set one with tenant_context(...), or wrap deliberate "
            "cross-tenant access in system_context()."
        )
        raise TenantContextRequiredError(message)

    def _apply_soft_delete_filter(self, query, include_deleted: bool = False) -> Any:  # noqa: ANN401
        """Apply soft delete filter to query.

        Args:
            query: SQLAlchemy query to modify
            include_deleted: Whether to include soft-deleted records

        Returns:
            Query with soft delete filter applied
        """
        if include_deleted:
            return query

        if hasattr(self.model, "deleted_at"):
            model_deleted_at = getattr(self.model, "deleted_at", None)
            if model_deleted_at is not None:
                return query.where(model_deleted_at.is_(None))

        return query

    async def create(self, data: dict[str, Any]) -> T:
        """Create a new record.

        Args:
            data: Dictionary of field values

        Returns:
            Created model instance
        """
        # Add tenant_id if model has it and not already set. Treat an
        # explicit `None` the same as absent: callers that default an
        # optional `tenant_id` parameter to `None` (rather than omitting
        # the key) must not defeat auto-fill from the active context.
        #
        # Falls back to SYSTEM_TENANT_ID rather than leaving the column
        # NULL when there is no active context either: a row written as
        # NULL can only ever be found again by a query that also happens to
        # run with no tenant context, which _apply_tenant_filter() no
        # longer allows silently. SYSTEM_TENANT_ID is a real, queryable
        # tenant (see initial_data.py), the same fallback already used for
        # a tenant-less user's audit log entries.
        if hasattr(self.model, "tenant_id") and data.get("tenant_id") is None:
            data["tenant_id"] = get_current_tenant_id() or SYSTEM_TENANT_ID

        # Set timestamps.
        now = datetime.now(UTC)
        if "created_at" not in data:
            data["created_at"] = now
        if "updated_at" not in data:
            data["updated_at"] = now

        # Create instance.
        database_object = self.model(**data)
        self.session.add(database_object)
        await self.session.flush()
        await self.session.refresh(database_object)

        logger.debug(
            "record_created",
            model=self.model.__name__,
            id=str(getattr(database_object, "id", None)),
        )
        return database_object

    async def get(self, id: UUID | str, include_deleted: bool = False) -> T | None:
        """Get record by ID.

        Args:
            id: Record UUID
            include_deleted: Whether to include soft-deleted records

        Returns:
            Model instance or None if not found
        """
        model_id = cast(Any, self._model_with_columns.id)
        query = select(self.model).where(model_id == id)
        query = self._apply_tenant_filter(query)
        query = self._apply_soft_delete_filter(query, include_deleted)

        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def list(
        self,
        skip: int = 0,
        limit: int = 20,
        filters: dict[str, Any] | None = None,
        sort_by: str | None = None,
        sort_order: str = "asc",
        include_deleted: bool = False,
    ) -> tuple[Sequence[T], int]:
        """List records with pagination and filtering.

        Args:
            skip: Number of records to skip
            limit: Maximum records to return
            filters: Additional filter conditions
            sort_by: Field to sort by
            sort_order: Sort direction (asc/desc)
            include_deleted: Whether to include soft-deleted records

        Returns:
            Tuple of (records, total_count)
        """
        # Carry the total matching-row count alongside each row via a window
        # function, instead of a separate `SELECT count(*)` round trip.
        total_count_column = func.count().over().label("_total_count")
        query = select(self.model, total_count_column)

        # Apply filters.
        query = self._apply_tenant_filter(query)
        query = self._apply_soft_delete_filter(query, include_deleted)

        # Apply additional filters.
        filter_conditions = []
        if filters:
            for field, value in filters.items():
                if hasattr(self.model, field):
                    if value is not None:
                        filter_conditions.append(getattr(self.model, field) == value)
            if filter_conditions:
                query = query.where(and_(*filter_conditions))

        # Apply sorting. Only mapped columns qualify: `hasattr` also matched
        # `metadata` and every model method, which `order_by` then rejected
        # at query-build time.
        if sort_by and sort_by in cast(Any, self.model).__table__.columns:
            sort_column = cast(Any, getattr(self.model, sort_by))
            if sort_order.lower() == "desc":
                sort_column = sort_column.desc()
            query = query.order_by(sort_column)
        else:
            created_at = cast(Any, self._model_with_columns.created_at)
            query = query.order_by(created_at.desc())

        # Apply pagination.
        limit = min(limit, MAX_PAGE_SIZE)
        query = query.offset(skip).limit(limit)

        # Execute.
        result = await self.session.execute(query)
        rows = result.all()
        records = [cast(T, row[0]) for row in rows]

        if rows:
            # The window function count rides along with every returned row.
            total = cast(int, rows[0][1])
        else:
            # `skip` landed past the end (or zero matches): the window
            # function contributes no row, so fall back to a plain count.
            count_query = select(func.count()).select_from(self.model)
            count_query = self._apply_tenant_filter(count_query)
            count_query = self._apply_soft_delete_filter(count_query, include_deleted)
            if filter_conditions:
                count_query = count_query.where(and_(*filter_conditions))
            count_result = await self.session.execute(count_query)
            total = count_result.scalar() or 0

        return records, total

    async def update(self, id: UUID | str, data: dict[str, Any]) -> T | None:
        """Update record by ID.

        Note: concurrent updates to the same record are subject to last-write-wins
        race conditions. For critical data, use optimistic locking (version column)
        or SELECT ... FOR UPDATE.

        Args:
            id: Record UUID
            data: Dictionary of field values to update

        Returns:
            Updated model instance or None if not found
        """
        database_object = await self.get(id)
        if not database_object:
            return None

        # Set updated_at.
        data["updated_at"] = datetime.now(UTC)

        # Update fields.
        for key, value in data.items():
            if hasattr(database_object, key) and key != "id":
                setattr(database_object, key, value)

        await self.session.flush()
        await self.session.refresh(database_object)

        logger.debug("record_updated", model=self.model.__name__, id=str(id))
        return database_object

    async def delete(self, id: UUID | str, hard: bool = False) -> bool:
        """Delete record by ID.

        Args:
            id: Record UUID
            hard: If True, permanently delete. If False, soft delete.

        Returns:
            True if deleted, False if not found
        """
        database_object = await self.get(id)
        if not database_object:
            return False

        if hard:
            await self.session.delete(database_object)
        else:
            async with self.session.begin_nested():
                database_object.deleted_at = datetime.now(UTC)
                database_object.updated_at = datetime.now(UTC)
                await self._bury(database_object, id)

        await self.session.flush()

        logger.debug("record_deleted", model=self.model.__name__, id=str(id), hard=hard)
        return True

    async def _bury(self, database_object: T, id: UUID | str) -> None:
        """Persist a graveyard snapshot for a soft-deleted record."""
        from app.repositories.graveyard_repository import GraveyardRepository

        mapper = cast(Mapper[Any], inspect(database_object).mapper)  # type: ignore[union-attr]
        data = {
            column.key: _jsonable(getattr(database_object, column.key))
            for column in mapper.column_attrs
        }

        tenant_id = getattr(database_object, "tenant_id", None)
        if tenant_id is None:
            tenant_id = SYSTEM_TENANT_ID
        record_id = getattr(database_object, "id", id)

        if isinstance(record_id, str):
            record_id = UUID(record_id)
        elif not isinstance(record_id, UUID):
            record_id = str(record_id)

        await GraveyardRepository(self.session).bury(
            model_name=str(self._model_with_columns.__tablename__),
            record_id=record_id,  # type: ignore[arg-type]
            data=data,
            tenant_id=tenant_id,
        )

    async def exists(self, id: UUID | str) -> bool:
        """Check if record exists.

        Args:
            id: Record UUID

        Returns:
            True if exists
        """
        model_id = cast(Any, self._model_with_columns.id)
        query = select(func.count()).select_from(self.model).where(model_id == id)
        query = self._apply_tenant_filter(query)
        query = self._apply_soft_delete_filter(query)

        result = await self.session.execute(query)
        return (result.scalar() or 0) > 0
