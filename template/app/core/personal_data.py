"""Which rows and columns a person's data export carries.

Article 20 asks for everything held about the person, and a hand-written
list of tables falls behind the first time someone adds one. So the export
walks every column that points at the account instead, and a test fails
until each such column is classified here or in
`app.core.personal_data_project`: exported, or withheld with a reason.

Withholding is a real choice, not a gap. A row can point at the person
without being theirs to read: who blocked them, for one.
"""

from __future__ import annotations

import re

from app.core.personal_data_project import PROJECT_EXPORTED
from app.core.personal_data_project import PROJECT_WITHHELD

USER_TABLE = "users"

# Column names that carry credentials or their derivatives, left out of
# every exported row. Matched on whole name parts, so `token` drops
# `token_version` along with a device token, and `provider_user_id` stays.
SECRET_COLUMN = re.compile(r"(^|_)(hash|hashed|password|secret|token|key)(_|$)")

# `table.column` to the table its value points into. `users` means the
# account itself, any other table means the rows of that table already
# exported, so a child row follows its parent into the export.
EXPORTED: dict[str, str] = {
    "api_keys.user_id": USER_TABLE,
    "consent_records.user_id": USER_TABLE,
    "device_tokens.user_id": USER_TABLE,
    "files.uploaded_by": USER_TABLE,
    "notification_preferences.user_id": USER_TABLE,
    "oauth_accounts.user_id": USER_TABLE,
    "webauthn_credentials.user_id": USER_TABLE,
    **PROJECT_EXPORTED,
}

# `table.column` to why its rows stay out of the export.
WITHHELD: dict[str, str] = {
    "totp_recovery_codes.user_id": "Only hashes of single-use codes, nothing readable is left once the secret columns go.",
    **PROJECT_WITHHELD,
}
