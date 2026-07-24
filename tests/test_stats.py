"""Unit tests for the statistics pure service (Phase 4)."""

from __future__ import annotations

from bot.services.stats import StatsService


class TestPercentage:
    def test_normal(self):
        assert StatsService.percentage(25, 100) == 25.0

    def test_rounding(self):
        assert StatsService.percentage(1, 3) == 33.3

    def test_zero_total(self):
        assert StatsService.percentage(5, 0) == 0.0

    def test_negative_total(self):
        assert StatsService.percentage(5, -10) == 0.0


class TestMedal:
    def test_top_three(self):
        assert StatsService.medal(1) == "🥇"
        assert StatsService.medal(2) == "🥈"
        assert StatsService.medal(3) == "🥉"

    def test_rest(self):
        assert StatsService.medal(4) == "4."
        assert StatsService.medal(10) == "10."


class TestClampTop:
    def test_none_uses_default(self):
        assert StatsService.clamp_top(None, 10, 25) == 10

    def test_zero_uses_default(self):
        assert StatsService.clamp_top(0, 10, 25) == 10

    def test_negative_uses_default(self):
        assert StatsService.clamp_top(-5, 10, 25) == 10

    def test_within_range(self):
        assert StatsService.clamp_top(7, 10, 25) == 7

    def test_over_max_clamped(self):
        assert StatsService.clamp_top(999, 10, 25) == 25
