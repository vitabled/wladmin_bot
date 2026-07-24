"""Pure helpers for activity statistics / leaderboards (Phase 4)."""

from __future__ import annotations

_MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


class StatsService:
    """Stateless formatting/derivation helpers for activity reports."""

    @staticmethod
    def percentage(count: int, total: int) -> float:
        """Share of ``count`` in ``total``, rounded to 1 decimal (0.0 if total<=0)."""
        if total <= 0:
            return 0.0
        return round(count * 100 / total, 1)

    @staticmethod
    def medal(rank: int) -> str:
        """Medal emoji for the top 3, otherwise ``N.`` position marker."""
        return _MEDALS.get(rank, f"{rank}.")

    @staticmethod
    def clamp_top(requested: int | None, default: int, maximum: int) -> int:
        """Clamp a requested leaderboard size into ``[1, maximum]``."""
        if not requested or requested <= 0:
            return default
        return min(requested, maximum)
