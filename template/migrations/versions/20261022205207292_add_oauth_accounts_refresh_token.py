"""Keep the provider's refresh token on a linked OAuth account.

Sign in with Apple has to be revoked when the account is deleted, and
Apple's revoke endpoint takes the refresh token from the code exchange,
issued to one client ID, the app's bundle ID or the web Services ID.

Revision ID: 20261022205207292
Revises: 20261022130759210
Create Date: 2026-10-22 20:52:07.292000

"""

from typing import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic.
revision: str = "20261022205207292"
down_revision: Union[str, None] = "20261022130759210"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Encrypted at rest by `EncryptedString`, the column holds ciphertext.
    op.add_column(
        "oauth_accounts",
        sa.Column("refresh_token", sa.String(length=1024), nullable=True),
    )
    op.add_column(
        "oauth_accounts",
        sa.Column("refresh_token_client_id", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("oauth_accounts", "refresh_token_client_id")
    op.drop_column("oauth_accounts", "refresh_token")
