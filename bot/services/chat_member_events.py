"""Pure classification of Telegram membership status transitions."""

from typing import Literal


def classify_transition(
    old_status: str | None, new_status: str
) -> Literal["joined", "left", "ignored"]:
    """Classify joins and departures without treating promotions as joins."""
    present = ("member", "restricted", "administrator")
    absent = ("left", "kicked")
    if old_status in (None, *absent) and new_status in present:
        return "joined"
    if old_status in present and new_status in absent:
        return "left"
    return "ignored"
