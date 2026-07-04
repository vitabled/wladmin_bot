import pytest
from datetime import datetime, timedelta

from bot.services.moderation import ModerationService


class TestModerationService:
    """Tests for moderation logic."""

    def test_parse_duration_minutes(self):
        """Test parsing duration in minutes."""
        assert ModerationService.parse_duration("30m") == 1800
        assert ModerationService.parse_duration("10m") == 600

    def test_parse_duration_hours(self):
        """Test parsing duration in hours."""
        assert ModerationService.parse_duration("2h") == 7200
        assert ModerationService.parse_duration("1h") == 3600

    def test_parse_duration_days(self):
        """Test parsing duration in days."""
        assert ModerationService.parse_duration("1d") == 86400
        assert ModerationService.parse_duration("7d") == 604800

    def test_parse_duration_none(self):
        """Test parsing None returns None."""
        assert ModerationService.parse_duration(None) is None

    def test_parse_duration_empty(self):
        """Test parsing empty string returns None."""
        assert ModerationService.parse_duration("") is None

    def test_parse_duration_invalid(self):
        """Test parsing invalid duration returns None."""
        assert ModerationService.parse_duration("abc") is None
        assert ModerationService.parse_duration("30x") is None

    def test_parse_duration_case_insensitive(self):
        """Test parsing is case insensitive."""
        assert ModerationService.parse_duration("30M") == 1800
        assert ModerationService.parse_duration("2H") == 7200

    def test_get_unban_date_permanent(self):
        """Test that None duration returns None (permanent ban)."""
        assert ModerationService.get_unban_date(None) is None

    def test_get_unban_date_temporary(self):
        """Test temporary ban unban date calculation."""
        unban_date = ModerationService.get_unban_date(3600)
        now = datetime.utcnow()
        assert unban_date is not None
        assert unban_date > now
        assert unban_date < now + timedelta(seconds=3700)

    def test_should_auto_unban_permanent(self):
        """Test permanent ban should not auto-unban."""
        assert not ModerationService.should_auto_unban(None)

    def test_should_auto_unban_expired(self):
        """Test expired ban should auto-unban."""
        past_date = datetime.utcnow() - timedelta(seconds=10)
        assert ModerationService.should_auto_unban(past_date)

    def test_should_auto_unban_not_expired(self):
        """Test non-expired ban should not auto-unban."""
        future_date = datetime.utcnow() + timedelta(seconds=3600)
        assert not ModerationService.should_auto_unban(future_date)

    def test_validate_command_args_single(self):
        """Test parsing single argument."""
        args = ModerationService.validate_command_args("@user")
        assert args == ["@user"]

    def test_validate_command_args_multiple(self):
        """Test parsing multiple arguments."""
        args = ModerationService.validate_command_args("@user 30m reason here")
        assert args[0] == "@user"
        assert args[1] == "30m"
        assert "reason" in args[2]

    def test_validate_command_args_empty(self):
        """Test parsing empty arguments."""
        args = ModerationService.validate_command_args("")
        assert args == []
