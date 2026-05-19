"""Initial data script for creating superuser on first startup."""

import asyncio
import logging

from sqlmodel import select

from app.core.security import generate_password_hash
from app.core.settings import settings
from app.database import async_session_factory
from app.models.user import User

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def init() -> None:
    """Create initial superuser if not exists."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(User).where(User.email == settings.FIRST_SUPERUSER_EMAIL)
        )
        existing_user = result.scalar_one_or_none()

        if not existing_user:
            password_hash = generate_password_hash(settings.FIRST_SUPERUSER_PASSWORD)
            user = User(
                email=settings.FIRST_SUPERUSER_EMAIL,
                hashed_password=password_hash,
                full_name=settings.FIRST_SUPERUSER_FULL_NAME,
                is_superuser=True,
                is_active=True,
                is_verified=True,
            )
            session.add(user)
            await session.commit()
            logger.info("Created superuser: %s", settings.FIRST_SUPERUSER_EMAIL)
        else:
            logger.info("Superuser already exists: %s", settings.FIRST_SUPERUSER_EMAIL)


def main() -> None:
    """Create initial data in database."""
    logger.info("Creating initial data...")
    asyncio.run(init())
    logger.info("Initial data creation complete")


if __name__ == "__main__":
    main()
