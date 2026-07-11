"""Unit tests for compute_closed_period (ADR-0033) — pure, DB-free.

The end-to-end aggregation (real Postgres) lives in tests/e2e/test_tax_sim_runner.py.
"""

from __future__ import annotations

from datetime import UTC, datetime

from aiat.orchestration.tax_sim_runner import compute_closed_period


class TestComputeClosedPeriodDaily:
    def test_previous_full_day(self) -> None:
        label, start, end = compute_closed_period(datetime(2026, 7, 11, 0, 5, tzinfo=UTC), "daily")
        assert label == "2026-07-10"
        assert start == datetime(2026, 7, 10, 0, 0, tzinfo=UTC)
        assert end == datetime(2026, 7, 11, 0, 0, tzinfo=UTC)

    def test_month_boundary(self) -> None:
        label, start, end = compute_closed_period(datetime(2026, 3, 1, 0, 5, tzinfo=UTC), "daily")
        assert label == "2026-02-28"
        assert start == datetime(2026, 2, 28, 0, 0, tzinfo=UTC)
        assert end == datetime(2026, 3, 1, 0, 0, tzinfo=UTC)


class TestComputeClosedPeriodQuarter:
    def test_q2_returns_q1(self) -> None:
        label, start, end = compute_closed_period(datetime(2026, 5, 20, tzinfo=UTC), "quarter")
        assert label == "Q1-2026"
        assert start == datetime(2026, 1, 1, tzinfo=UTC)
        assert end == datetime(2026, 4, 1, tzinfo=UTC)

    def test_q3_returns_q2(self) -> None:
        label, start, end = compute_closed_period(datetime(2026, 8, 1, tzinfo=UTC), "quarter")
        assert label == "Q2-2026"
        assert start == datetime(2026, 4, 1, tzinfo=UTC)
        assert end == datetime(2026, 7, 1, tzinfo=UTC)

    def test_q1_returns_prev_year_q4(self) -> None:
        label, start, end = compute_closed_period(datetime(2026, 2, 10, tzinfo=UTC), "quarter")
        assert label == "Q4-2025"
        assert start == datetime(2025, 10, 1, tzinfo=UTC)
        assert end == datetime(2026, 1, 1, tzinfo=UTC)
