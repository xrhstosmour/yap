from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config
from sqlalchemy import pool

from app.core.settings import settings
from app.models.base import SQLModel

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata

# Respect an externally injected URL such as the test harness targeting a
# per-worker database. Otherwise fall back to the application settings.
if not config.get_main_option("sqlalchemy.url"):
    # `set_main_option` writes through configparser, which treats `%` as the
    # start of an interpolation. A percent-encoded credential, which is what
    # any password holding a reserved character produces, raised
    # `ValueError: invalid interpolation syntax` and killed every migration.
    # Doubling it is the documented escape and reads back unchanged.
    config.set_main_option(
        "sqlalchemy.url", str(settings.DATABASE_URI).replace("%", "%%")
    )


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
