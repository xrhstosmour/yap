"""Tests for backend_pre_start: database readiness check with tenacity retry."""

from __future__ import annotations

from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from tenacity import RetryError
from tenacity import Retrying


class TestBackendPreStart:
    """Tests for the backend_pre_start database readiness script."""

    # Import

    def test_init_and_main_can_be_imported(self) -> None:
        """The init and main functions can be imported without errors."""
        from app.backend_pre_start import init
        from app.backend_pre_start import main

        assert callable(init)
        assert callable(main)

    # Decorator configuration

    def test_retry_decorator_is_configured(self) -> None:
        """The init function is wrapped by tenacity with sync Retrying."""
        from app.backend_pre_start import init

        assert hasattr(init, "__wrapped__"), (
            "init should expose the original function via __wrapped__"
        )
        assert hasattr(init, "retry"), (
            "init should expose tenacity retry state via .retry"
        )
        assert isinstance(init.retry, Retrying), (
            "init.retry should be a tenacity Retrying instance (sync)"
        )

    # Happy path

    def test_init_succeeds_when_db_is_ready(self) -> None:
        """init() completes without error when the database responds."""
        from app.backend_pre_start import init

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=None)
        mock_session.exec = MagicMock()  # no exception – success
        mock_engine = MagicMock()

        with patch("app.backend_pre_start.Session", return_value=mock_session):
            # Should not raise.
            init(mock_engine)

        mock_session.exec.assert_called_once()

    # Error handling – unwrapped function

    def test_unwrapped_init_raises_on_connection_failure(self) -> None:
        """The undecorated init re-raises connection errors."""
        from app.backend_pre_start import init

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=None)
        mock_session.exec = MagicMock(
            side_effect=Exception("connection failed"),
        )
        mock_engine = MagicMock()

        with patch("app.backend_pre_start.Session", return_value=mock_session):
            with pytest.raises(Exception, match="connection failed"):
                init.__wrapped__(mock_engine)

        mock_session.exec.assert_called_once()

    # Retry behaviour

    def test_init_retries_and_raises_on_persistent_failure(self) -> None:
        """init() retries on failure and raises RetryError after exhausting.

        Uses the real retry configuration (300 attempts × 1 s wait).
        ``time.sleep`` is patched so the test completes in milliseconds
        rather than 5 minutes.
        """
        from app.backend_pre_start import init

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=None)
        mock_session.exec = MagicMock(
            side_effect=Exception("connection failed"),
        )
        mock_engine = MagicMock()

        with (
            patch("app.backend_pre_start.Session", return_value=mock_session),
            patch("time.sleep"),
        ):  # skip tenacity wait
            with pytest.raises(RetryError):
                init(mock_engine)

        # Retried multiple times, the exact count matches max_tries (300).
        assert mock_session.exec.call_count > 1
