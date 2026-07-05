from dataclasses import dataclass


@dataclass
class WarnResult:
    """Result of warn operation."""

    warned: bool
    warn_count: int
    limit_reached: bool
    action_triggered: str | None = None


class WarnsService:
    """Pure business logic for warn system."""

    @staticmethod
    def calculate_warn_count(active_warns: int, warn_limit: int) -> tuple[int, bool]:
        """Calculate total warn count and check if limit is reached."""
        return active_warns, active_warns >= warn_limit

    @staticmethod
    def should_apply_action(warn_count: int, warn_limit: int) -> bool:
        """Determine if warn limit is reached and action should be applied."""
        return warn_count >= warn_limit

    @staticmethod
    def get_action(
        warn_action: str, warn_action_duration: int | None = None
    ) -> tuple[str, int | None]:
        """Get moderation action to apply."""
        return warn_action, warn_action_duration
