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
    """Create Metabase database and read-only user if enabled."""
    password = os.environ.get("METABASE_READ_ONLY_PASSWORD")
    if not password:
        return

    try:
        _setup_metabase(password)
    except Exception as e:
        logger.warning("Metabase database setup failed: %s", e)


def _setup_metabase(password: str) -> None:
    base_url = str(settings.DATABASE_URI)
    admin_url = base_url.rsplit("/", 1)[0] + "/postgres"
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")

    with engine.connect() as conn:
        try:
            conn.execute(text("CREATE DATABASE metabase"))
        except Exception:
            pass

        try:
            # `CREATE USER ... PASSWORD` is DDL and cannot take a bound
            # parameter, so the password literal is quoted via psycopg's
            # SQL-literal quoting instead of raw f-string interpolation.
            quoted_password = psycopg_sql.Literal(password).as_string(None)
            conn.execute(
                text(f"CREATE USER metabase_readonly WITH PASSWORD {quoted_password}"),
            )
        except Exception:
            logger.warning("metabase_readonly user already exists!")

        try:
            conn.execute(
                text(
                    "GRANT CONNECT ON DATABASE "
                    f'"{settings.POSTGRESQL_DATABASE}" TO metabase_readonly'
                ),
            )
        except Exception:
            pass

    engine.dispose()

    app_engine = create_engine(str(settings.DATABASE_URI), isolation_level="AUTOCOMMIT")
    with app_engine.connect() as app_conn:
        try:
            app_conn.execute(
                text(
                    "GRANT SELECT ON ALL TABLES IN SCHEMA public TO metabase_readonly"
                ),
            )
            app_conn.execute(
                text(
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                    "GRANT SELECT ON TABLES TO metabase_readonly"
                ),
            )
        except Exception:
            pass
    app_engine.dispose()
    logger.info("Metabase database setup complete")


def main() -> None:
    logger.info("Creating initial data...")
    asyncio.run(init())
    setup_metabase()
    logger.info("Initial data creation complete")


if __name__ == "__main__":
    main()
