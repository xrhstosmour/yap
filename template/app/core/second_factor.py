"""The rule for when a superuser must have a second factor.

Lives here, not in `app.dependencies`, so a service can apply the same rule
without importing FastAPI. Two copies of it would drift, and the API would
then disagree with itself about who a moderator is.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.settings import settings

if TYPE_CHECKING:
    from app.models.user import User


def lacks_required_second_factor(user: User) -> bool:
    """Whether a superuser is on a password alone where that is not allowed.

    `/admin` already refuses such a superuser outside `local`, and the API
    must too: it reaches the same powers (feature flags, tenants, metrics),
    so leaving it open made a password alone the way around the admin gate.
    """
    return settings.ENVIRONMENT != "local" and not user.is_2fa_enabled
