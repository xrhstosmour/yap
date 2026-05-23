"""Backend pre-start script for database initialization.

This script waits for the database to be ready before proceeding.
It uses tenacity for retry logic to handle database startup delays.
"""

import logging

from sqlalchemy import Engine
from sqlmodel import Session
from sqlmodel import select
from tenacity import after_log
from tenacity import before_log
from tenacity import retry
from tenacity import stop_after_attempt
from tenacity import wait_fixed

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

max_tries = 60 * 5  # 5 minutes
wait_seconds = 1


@retry(
    stop=stop_after_attempt(max_tries),
    wait=wait_fixed(wait_seconds),
    before=before_log(logger, logging.INFO),
    after=after_log(logger, logging.WARN),
)
def init(db_engine: Engine) -> None:
    """Check if database is ready by executing a simple query."""
    try:
        with Session(db_engine) as session:
            session.exec(select(1))
    except Exception as e:
        logger.error(e)
        raise


def main() -> None:
    """Wait for database to be ready."""
    from app.database import sync_engine

    logger.info("Waiting for database to be ready...")
    init(sync_engine)
    logger.info("Database is ready")


if __name__ == "__main__":
    main()
