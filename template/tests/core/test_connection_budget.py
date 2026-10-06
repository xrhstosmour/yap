"""Tests the Postgres connection budget.

The pool is sized per process, and nothing multiplied that by the number of
processes before comparing it against what the server grants. A deployment
could therefore ask for more connections than existed and only find out under
load, as connection errors that read like the database being down.

The arithmetic is tested without a database on purpose: the bug was in the
multiplication, not in the query.
"""

from __future__ import annotations

import pytest

from app.core.connection_budget import WORKERS_VARIABLE
from app.core.connection_budget import ConnectionCeiling
from app.core.connection_budget import engines_per_worker
from app.core.connection_budget import over_budget
from app.core.connection_budget import worker_count
from app.core.connection_budget import worst_case_demand


class TestWorkerCount:
    """The variable the app never read."""

    def test_an_absent_variable_means_one_process(self) -> None:
        """A bare `uvicorn app.main:app` runs one."""
        assert worker_count({}) == 1

    def test_a_count_is_read(self) -> None:
        """This is the number the whole budget scales by."""
        assert worker_count({WORKERS_VARIABLE: "4"}) == 4

    @pytest.mark.parametrize("value", ["", "four", "-2", "0"])
    def test_an_unusable_value_falls_back_to_one(self, value: str) -> None:
        """Guessing high would refuse to start a deployment that was fine."""
        assert worker_count({WORKERS_VARIABLE: value}) == 1


class TestEnginesPerWorker:
    """A second database doubles what each process holds."""

    def test_one_engine_by_default(self) -> None:
        assert engines_per_worker(False) == 1

    def test_two_when_a_second_database_is_configured(self) -> None:
        """`additional_async_engine` is pooled exactly like the main one."""
        assert engines_per_worker(True) == 2


class TestWorstCaseDemand:
    """Overflow counts, because a burst is what opens it."""

    def test_overflow_is_included(self) -> None:
        """Sizing against `pool_size` alone is what hides the ceiling."""
        assert worst_case_demand(10, 20, 1, False) == 30

    def test_workers_multiply(self) -> None:
        """The template default of 10 + 20 across 4 workers is 120."""
        assert worst_case_demand(10, 20, 4, False) == 120

    def test_a_second_database_doubles_it(self) -> None:
        assert worst_case_demand(10, 20, 2, True) == 120

    def test_no_overflow_is_just_the_pool(self) -> None:
        assert worst_case_demand(5, 0, 3, False) == 15


class TestOverBudget:
    """What refusing to start depends on."""

    def test_a_fitting_demand_passes(self) -> None:
        assert over_budget(30, ConnectionCeiling(100, 3)) is None

    def test_exactly_filling_the_ceiling_passes(self) -> None:
        """97 of 97 is tight but legal, and refusing it would be wrong."""
        assert over_budget(97, ConnectionCeiling(100, 3)) is None

    def test_one_over_is_refused(self) -> None:
        assert over_budget(98, ConnectionCeiling(100, 3)) is not None

    def test_the_reserved_slots_are_not_available(self) -> None:
        """A superuser reservation is not capacity an application can use."""
        assert ConnectionCeiling(100, 3).usable == 97

    def test_the_template_default_at_four_workers_is_refused(self) -> None:
        """The case that prompted this: 4 x (10 + 20) against 100."""
        demand = worst_case_demand(10, 20, 4, False)
        assert over_budget(demand, ConnectionCeiling(100, 3)) is not None

    def test_the_message_names_the_numbers_and_the_knobs(self) -> None:
        """An error nobody can act on is barely better than the failure."""
        complaint = over_budget(120, ConnectionCeiling(100, 3))
        assert complaint is not None
        for fragment in (
            "120",
            "97",
            "100",
            "DATABASE_POOL_SIZE",
            "DATABASE_MAX_OVERFLOW",
            WORKERS_VARIABLE,
        ):
            assert fragment in complaint, f"{fragment} is missing"

    def test_a_ceiling_smaller_than_its_reservation_is_not_negative(self) -> None:
        """Nonsense configuration should not produce nonsense capacity."""
        assert ConnectionCeiling(2, 3).usable == 0
