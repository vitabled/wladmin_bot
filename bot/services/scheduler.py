"""Pure timing logic for scheduled posts (Phase 5).

Все времена — timezone-aware UTC. ``now`` всегда передаётся явно, чтобы логика
была детерминированной и тестируемой без обращения к часам.
"""

from __future__ import annotations

from datetime import datetime, timedelta


class SchedulerService:
    """Stateless helpers deciding when a scheduled post runs next."""

    @staticmethod
    def is_due(run_at: datetime, now: datetime) -> bool:
        """True when the post's scheduled time has arrived."""
        return run_at <= now

    @staticmethod
    def next_run(
        run_at: datetime,
        interval_seconds: int | None,
        now: datetime,
    ) -> datetime | None:
        """Next run time strictly after ``now`` for a recurring post, else None.

        Catches up if several intervals were missed (e.g. bot was down), so the
        post fires once and is rescheduled to the next future slot, not spammed
        for every missed window.
        """
        if not interval_seconds or interval_seconds <= 0:
            return None
        step = timedelta(seconds=interval_seconds)
        nxt = run_at + step
        while nxt <= now:
            nxt += step
        return nxt
