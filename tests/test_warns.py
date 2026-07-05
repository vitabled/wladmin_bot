from bot.services.warns import WarnsService


class TestWarnsService:
    """Tests for warn system logic."""

    def test_calculate_warn_count_below_limit(self):
        """Test warn count below limit."""
        count, limit_reached = WarnsService.calculate_warn_count(
            active_warns=1, warn_limit=3
        )
        assert count == 1
        assert not limit_reached

    def test_calculate_warn_count_at_limit(self):
        """Test warn count at limit."""
        count, limit_reached = WarnsService.calculate_warn_count(
            active_warns=3, warn_limit=3
        )
        assert count == 3
        assert limit_reached

    def test_calculate_warn_count_over_limit(self):
        """Test warn count over limit."""
        count, limit_reached = WarnsService.calculate_warn_count(
            active_warns=4, warn_limit=3
        )
        assert count == 4
        assert limit_reached

    def test_should_apply_action_true(self):
        """Test action should be applied."""
        assert WarnsService.should_apply_action(warn_count=3, warn_limit=3)

    def test_should_apply_action_false(self):
        """Test action should not be applied."""
        assert not WarnsService.should_apply_action(warn_count=2, warn_limit=3)

    def test_get_action_mute(self):
        """Test mute action."""
        action, duration = WarnsService.get_action("mute", 3600)
        assert action == "mute"
        assert duration == 3600

    def test_get_action_permanent(self):
        """Test permanent action."""
        action, duration = WarnsService.get_action("ban", None)
        assert action == "ban"
        assert duration is None
