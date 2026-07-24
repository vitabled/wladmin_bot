"""Unit tests for the scheduler pure service (Phase 5)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from bot.services.scheduler import SchedulerService

_NOW = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)


class TestIsDue:
    def test_past_is_due(self):
        assert SchedulerService.is_due(_NOW - timedelta(seconds=1), _NOW)

    def test_exactly_now_is_due(self):
        assert SchedulerService.is_due(_NOW, _NOW)

    def test_future_not_due(self):
        assert not SchedulerService.is_due(_NOW + timedelta(seconds=1), _NOW)


class TestNextRun:
    def test_one_off_returns_none(self):
        assert SchedulerService.next_run(_NOW, None, _NOW) is None

    def test_zero_interval_returns_none(self):
        assert SchedulerService.next_run(_NOW, 0, _NOW) is None

    def test_recurring_advances_one_interval(self):
        nxt = SchedulerService.next_run(_NOW, 3600, _NOW)
        assert nxt == _NOW + timedelta(seconds=3600)

    def test_catches_up_missed_intervals(self):
        # run_at 3h ago, hourly interval → next slot is strictly in the future,
        # and no more than one interval ahead of now.
        run_at = _NOW - timedelta(hours=3)
        nxt = SchedulerService.next_run(run_at, 3600, _NOW)
        assert nxt > _NOW
        assert nxt - _NOW <= timedelta(seconds=3600)
