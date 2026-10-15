"""Tests what a person's data export collects from the database."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.consent_record import ConsentRecord
from app.models.device_token import DevicePlatform
from app.models.device_token import DeviceToken
from app.models.totp_recovery_code import TotpRecoveryCode
from app.models.user import User
from app.services.personal_data_service import PersonalDataService


async def _user(session: AsyncSession, email: str) -> User:
    user = User(email=email, hashed_password="hash", full_name="Export Person")
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


class TestCollect:
    """The export holds the person's rows and nobody else's."""

    async def test_the_account_comes_without_its_secrets(
        self, session: AsyncSession
    ) -> None:
        """The person reads their own row, minus what signs them in."""
        user = await _user(session, "account-export@example.com")

        collected = await PersonalDataService(session).collect(user.id)

        account = collected["account"]
        assert account["email"] == "account-export@example.com"
        assert account["full_name"] == "Export Person"
        assert "hashed_password" not in account
        assert "email_hash" not in account

    async def test_rows_pointing_at_the_person_are_included(
        self, session: AsyncSession
    ) -> None:
        """A consent and a device both lead back to the account."""
        user = await _user(session, "rows-export@example.com")
        session.add(ConsentRecord(user_id=user.id, kind="terms", is_granted=True))
        session.add(
            DeviceToken(
                user_id=user.id, token="push-token-value", platform=DevicePlatform.IOS
            )
        )
        await session.commit()

        records = (await PersonalDataService(session).collect(user.id))["records"]

        assert [row["kind"] for row in records["consent_records"]] == ["terms"]
        assert [row["platform"] for row in records["device_tokens"]] == ["ios"]
        assert "token" not in records["device_tokens"][0]

    async def test_another_persons_rows_stay_out(self, session: AsyncSession) -> None:
        """Rows are matched on the account, never on the table as a whole."""
        user = await _user(session, "mine-export@example.com")
        other = await _user(session, "theirs-export@example.com")
        session.add(ConsentRecord(user_id=other.id, kind="terms", is_granted=True))
        await session.commit()

        records = (await PersonalDataService(session).collect(user.id))["records"]

        assert records.get("consent_records", []) == []

    async def test_withheld_tables_are_left_out(self, session: AsyncSession) -> None:
        """Recovery code hashes are classified withheld and never appear."""
        user = await _user(session, "withheld-export@example.com")
        session.add(TotpRecoveryCode(user_id=user.id, code_hash="hash"))
        await session.commit()

        records = (await PersonalDataService(session).collect(user.id))["records"]

        assert "totp_recovery_codes" not in records
