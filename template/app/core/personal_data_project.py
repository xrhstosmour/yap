"""This project's own rows in a person's data export.

`app.core.personal_data` classifies the template's tables. Classify the
project's here, so the template can change its own list without this one
conflicting on every synchronisation. Same shape as there: a key is
`table.column`, and `test_every_user_reference_is_classified` fails until a
new column pointing at the person is in one of the two.
"""

from __future__ import annotations

# `table.column` to the table its value points into, `users` or another
# exported table, whose matching rows are then exported too.
PROJECT_EXPORTED: dict[str, str] = {}

# `table.column` to the reason its rows stay out of the export.
PROJECT_WITHHELD: dict[str, str] = {}
