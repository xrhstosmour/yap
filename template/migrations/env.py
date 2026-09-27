from logging.config import fileConfig

import sqlalchemy
from alembic import context
from sqlalchemy import engine_from_config
from sqlalchemy import pool

from app.core.settings import settings
from app.models.base import SQLModel

# Imported for its side effect: every model module has to be loaded before
# `SQLModel.metadata` is read, or autogenerate sees an empty schema and
# proposes dropping every table that is not in it.
import app.models  # noqa: F401  # isort: skip

# `UTCDateTime` only exists in newer `SQLModel` releases. Where it is
# absent, every `datetime` column maps to a plain `DateTime` and the
# comparison below has nothing to suppress.
try:
    from sqlmodel.sql.sqltypes import UTCDateTime
except ImportError:  # pragma: no cover - depends on the installed version.
    UTCDateTime = None

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


def _include_object(
    database_object,  # noqa: ANN001
    name,  # noqa: ANN001
    type_,  # noqa: ANN001
    reflected,  # noqa: ANN001, ARG001
    compare_to,  # noqa: ANN001, ARG001
) -> bool:
    """Hide objects autogenerate cannot see the definition of.

    The trigram indexes are created with raw `CREATE INDEX ... USING gin`
    inside earlier migrations rather than declared on a model, so every
    autogenerate run reads them as orphans and proposes dropping them.
    """
    del database_object
    return not (type_ == "index" and name is not None and name.endswith("_trgm"))


def _compare_type(
    context_,  # noqa: ANN001, ARG001
    inspected_column,  # noqa: ANN001, ARG001
    metadata_column,  # noqa: ANN001, ARG001
    inspected_type,  # noqa: ANN001
    metadata_type,  # noqa: ANN001
) -> bool | None:
    """Treat `SQLModel`'s `UTCDateTime` as the `TIMESTAMP` it compiles to.

    `UTCDateTime` is a `TypeDecorator` over `DateTime`, so it produces the
    same DDL, but autogenerate compares the Python types and reports every
    timestamp column in the schema as changed. Returning `False` for that
    pair suppresses the noise and leaves every other comparison to
    Alembic's default.
    """
    if (
        UTCDateTime is not None
        and isinstance(metadata_type, UTCDateTime)
        and isinstance(inspected_type, sqlalchemy.DateTime)
    ):
        return False
    return None


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=_include_object,
        compare_type=_compare_type,
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
            include_object=_include_object,
            compare_type=_compare_type,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
