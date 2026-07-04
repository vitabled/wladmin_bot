from datetime import datetime, timedelta
from typing import Optional


class ModerationService:
    """Pure business logic for moderation actions."""

    @staticmethod
    def parse_duration(duration_str: Optional[str]) -> Optional[int]:
        """
        Parse duration string to seconds.

        Examples: '30m' -> 1800, '2h' -> 7200, '1d' -> 86400
        Returns None for permanent bans.
        """
        if not duration_str:
            return None

        duration_str = duration_str.strip().lower()
        if not duration_str:
            return None

        try:
            value = int(duration_str[:-1])
        except (ValueError, IndexError):
            return None

        unit = duration_str[-1] if duration_str else ""

        if unit == "m":
            return value * 60
        elif unit == "h":
            return value * 3600
        elif unit == "d":
            return value * 86400
        else:
            return None

    @staticmethod
    def get_unban_date(
        duration_seconds: Optional[int],
    ) -> Optional[datetime]:
        """Calculate unban date from duration seconds."""
        if duration_seconds is None:
            return None

        return datetime.utcnow() + timedelta(seconds=duration_seconds)

    @staticmethod
    def should_auto_unban(unban_date: Optional[datetime]) -> bool:
        """Check if user should be auto-unbanned."""
        if unban_date is None:
            return False

        return datetime.utcnow() >= unban_date

    @staticmethod
    def validate_command_args(
        args: str, expected_count: Optional[int] = None
    ) -> list[str]:
        """Parse and validate command arguments."""
        if expected_count:
            parts = args.split(maxsplit=expected_count - 1)
        else:
            parts = args.split()
        return [p.strip() for p in parts if p.strip()]
