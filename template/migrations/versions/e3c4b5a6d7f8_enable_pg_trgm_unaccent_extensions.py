"""Enable PostgreSQL pg_trgm and unaccent extensions.

Creates the extensions, an IMMUTABLE unaccent wrapper for use in
functional indexes, and GIN trigram indexes on searchable columns.

Revision ID: e3c4b5a6d7f8
Revises: b0b1c2d3e4f5
Create Date: 2026-06-05 12:00:00.000000

"""

from typing import Sequence
from typing import Union

from alembic import op

# Revision identifiers, used by Alembic.
revision: str = "e3c4b5a6d7f8"
down_revision: Union[str, None] = "b0b1c2d3e4f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")

    # Create an IMMUTABLE wrapper around unaccent so it can be used in
    # functional indexes. PostgreSQL's built-in unaccent is STABLE, not
    # IMMUTABLE, which prevents it from appearing in index expressions.
    #
    # Both the function and its dictionary argument are schema-qualified.
    # PostgreSQL inlines this body while building the index, and index
    # expressions are evaluated under a restricted search path, so an
    # unqualified `unaccent($1)` fails with "function unaccent(text) does not
    # exist" even though the extension is installed. The single-argument form
    # also resolves its dictionary through the search path, hence the explicit
    # `regdictionary` cast.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.immutable_unaccent(text)
        RETURNS text
        IMMUTABLE PARALLEL SAFE STRICT
        LANGUAGE SQL
        AS $$ SELECT public.unaccent('public.unaccent'::regdictionary, $1) $$
        """
    )

    # GIN trigram index on users.email (required for trigram ILIKE / %).
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_users_email_trgm "
        "ON users USING gin (public.immutable_unaccent(email) gin_trgm_ops)"
    )

    # GIN trigram index on users.full_name (required for trigram ILIKE / %).
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_users_full_name_trgm "
        "ON users USING gin (public.immutable_unaccent(full_name) gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_users_full_name_trgm")
    op.execute("DROP INDEX IF EXISTS ix_users_email_trgm")
    op.execute("DROP FUNCTION IF EXISTS public.immutable_unaccent(text)")
    op.execute("DROP EXTENSION IF EXISTS unaccent")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
