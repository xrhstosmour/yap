"""Base repository with multi-tenant support and soft delete.

This module provides a generic repository class with common CRUD
operations, automatic tenant filtering, and soft delete support.
"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
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

from app.core.logging import get_logger
from app.core.tenant import get_current_tenant_id

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = get_logger("repository")

T = TypeVar("T", bound=SQLModel)


def _jsonable(value: Any) -> Any:
    """Convert model values to JSON-serializable primitives."""
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
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

    def _apply_tenant_filter(self, query) -> Any:
        """Apply tenant filter to query if model has tenant_id.

        For multi-tenant models, automatically filters to only
        return records belonging to the current tenant.

        Args:
            query: SQLAlchemy query to modify

        Returns:
            Query with tenant filter applied
        """
        tenant_id = get_current_tenant_id()

        if tenant_id is None:
            return query

        if hasattr(self.model, "tenant_id"):
            model_tenant_id = getattr(self.model, "tenant_id", None)
            if model_tenant_id is not None:
                return query.where(model_tenant_id == tenant_id)

        return query

    def _apply_soft_delete_filter(self, query, include_deleted: bool = False) -> Any:
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
        # Add tenant_id if model has it and not already set.
        tenant_id = get_current_tenant_id()
        if tenant_id and hasattr(self.model, "tenant_id"):
            if "tenant_id" not in data or data["tenant_id"] is None:
                data["tenant_id"] = tenant_id

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
        # Base query.
        query = select(self.model)
        count_query = select(func.count()).select_from(self.model)

        # Apply filters.
        query = self._apply_tenant_filter(query)
        count_query = self._apply_tenant_filter(count_query)
        query = self._apply_soft_delete_filter(query, include_deleted)
        count_query = self._apply_soft_delete_filter(count_query, include_deleted)

        # Apply additional filters.
        if filters:
            filter_conditions = []
            for field, value in filters.items():
                if hasattr(self.model, field):
                    if value is not None:
                        filter_conditions.append(getattr(self.model, field) == value)
            if filter_conditions:
                query = query.where(and_(*filter_conditions))
                count_query = count_query.where(and_(*filter_conditions))

        # Get total count.
        count_result = await self.session.execute(count_query)
        total = count_result.scalar() or 0

        # Apply sorting.
        if sort_by and hasattr(self.model, sort_by):
            sort_column = cast(Any, getattr(self.model, sort_by))
            if sort_order.lower() == "desc":
                sort_column = sort_column.desc()
            query = query.order_by(sort_column)
        else:
            created_at = cast(Any, self._model_with_columns.created_at)
            query = query.order_by(created_at.desc())

        # Apply pagination.
        query = query.offset(skip).limit(limit)

        # Execute.
        result = await self.session.execute(query)
        records = result.scalars().all()

        return records, total

    async def update(self, id: UUID | str, data: dict[str, Any]) -> T | None:
        """Update record by ID.

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
            tenant_id = UUID("00000000-0000-0000-0000-000000000000")
        record_id = getattr(database_object, "id", id)

        if isinstance(record_id, str):
            record_id = UUID(record_id)

        await GraveyardRepository(self.session).bury(
            model_name=str(self._model_with_columns.__tablename__),
            record_id=record_id,
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
