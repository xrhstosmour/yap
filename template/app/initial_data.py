"""Initial data script for creating superuser on first startup."""

import asyncio
import logging
import os

from psycopg import sql as psycopg_sql
from sqlalchemy import create_engine
from sqlmodel import select
from sqlmodel import text

from app.core import SYSTEM_TENANT_ID
from app.core.encryption import crypto
from app.core.security import generate_password_hash
from app.core.settings import settings
from app.database import async_session_factory
from app.models.tenant import Tenant
from app.models.user import User
from app.models.user import UserRole

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def init() -> None:
    """Create system tenant and initial superuser if not exists."""
    async with async_session_factory() as session:
        tenant_result = await session.execute(
            select(Tenant).where(Tenant.id == SYSTEM_TENANT_ID)
        )
        if not tenant_result.scalar_one_or_none():
            session.add(Tenant(id=SYSTEM_TENANT_ID, name="System", slug="system"))
            await session.commit()
            logger.info("Created system tenant")

        user_result = await session.execute(
            select(User).where(
                User.email_hash.in_(  # type: ignore[attr-defined]
                    crypto.hash_candidates_for_search(settings.FIRST_SUPERUSER_EMAIL)
                )
            )
        )
        existing_user = user_result.scalar_one_or_none()

        if not existing_user:
            password_hash = generate_password_hash(settings.FIRST_SUPERUSER_PASSWORD)
            user = User(
                email=settings.FIRST_SUPERUSER_EMAIL,
                hashed_password=password_hash,
                full_name=settings.FIRST_SUPERUSER_FULL_NAME,
                role=UserRole.SUPERUSER,
                is_active=True,
                is_verified=True,
                tenant_id=SYSTEM_TENANT_ID,
            )
            session.add(user)
            await session.commit()
            logger.info("Created super user")
        else:
            logger.info("Superuser already exists!")


def setup_metabase() -> None:
    """Create Metabase database and read-only user if enabled.

    Provisioning is best effort: Metabase is optional, so a failure here warns
    and lets the rest of the initial data finish. It does not pass silently,
    though. A setup that did not actually happen used to be indistinguishable
    from an idempotent re-run, and a `metabase_readonly` role that was never
    created was reported as "setup complete".
    """
    password = os.environ.get("METABASE_READ_ONLY_PASSWORD")
    if not password:
        return

    try:
        _setup_metabase(password)
    except Exception as error:
        logger.error(
            "Metabase provisioning failed, dashboards will not work until this "
            "is fixed: %s",
            error,
        )


# PostgreSQL SQLSTATEs for "that object is already there". These are the only
# failures this function may shrug off, because creating what already exists is
# exactly what a re-run does. Every other error means the provisioning did not
# happen and has to be reported.
DUPLICATE_DATABASE = "42P04"
DUPLICATE_OBJECT = "42710"


def _sqlstate(error: Exception) -> str | None:
    """Read the PostgreSQL error code out of a driver exception.

    Args:
        error: Exception raised by SQLAlchemy, wrapping a psycopg error.

    Returns:
        The five-character SQLSTATE, or None if this is not a database error.
    """
    return getattr(getattr(error, "orig", None), "sqlstate", None)


def _setup_metabase(password: str) -> None:
    """Create the Metabase database, read-only role and its grants.

    Args:
        password: Password to give the `metabase_readonly` role.

    Raises:
        RuntimeError: If the role does not exist once provisioning has run.
        Exception: Any database error other than the object already existing.
    """
    base_url = str(settings.DATABASE_URI)
    admin_url = base_url.rsplit("/", 1)[0] + "/postgres"
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")

    with engine.connect() as conn:
        try:
            conn.execute(text("CREATE DATABASE metabase"))
            logger.info("Created the Metabase application database")
        except Exception as error:
            if _sqlstate(error) != DUPLICATE_DATABASE:
                raise
            logger.info("Metabase application database already exists")

        try:
            # `CREATE USER ... PASSWORD` is DDL and cannot take a bound
            # parameter, so the password literal is quoted via psycopg's
            # SQL-literal quoting instead of raw f-string interpolation.
            quoted_password = psycopg_sql.Literal(password).as_string(None)
            conn.execute(
                text(f"CREATE USER metabase_readonly WITH PASSWORD {quoted_password}"),
            )
            logger.info("Created the metabase_readonly role")
        except Exception as error:
            if _sqlstate(error) != DUPLICATE_OBJECT:
                raise
            logger.info("The metabase_readonly role already exists")

        conn.execute(
            text(
                "GRANT CONNECT ON DATABASE "
                f'"{settings.POSTGRESQL_DATABASE}" TO metabase_readonly'
            ),
        )

    engine.dispose()

    app_engine = create_engine(str(settings.DATABASE_URI), isolation_level="AUTOCOMMIT")
    with app_engine.connect() as app_conn:
        app_conn.execute(
            text("GRANT SELECT ON ALL TABLES IN SCHEMA public TO metabase_readonly"),
        )
        app_conn.execute(
            text(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                "GRANT SELECT ON TABLES TO metabase_readonly"
            ),
        )

        # Checked rather than assumed, because the whole point of this rewrite
        # is that "complete" used to be printed over a role that was never
        # created, for instance when the connecting user has no CREATEROLE.
        exists = app_conn.execute(
            text("SELECT 1 FROM pg_roles WHERE rolname = 'metabase_readonly'")
        ).scalar_one_or_none()

    app_engine.dispose()

    if exists is None:
        raise RuntimeError(
            "The metabase_readonly role does not exist after provisioning. "
            f"Check that {settings.POSTGRESQL_USER} has CREATEROLE."
        )

    logger.info("Metabase database setup complete")


def main() -> None:
    logger.info("Creating initial data...")
    asyncio.run(init())
    setup_metabase()
    logger.info("Initial data creation complete")


if __name__ == "__main__":
    main()
