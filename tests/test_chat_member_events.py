"""Complete status transition table for membership events."""

import pytest

from bot.services.chat_member_events import classify_transition


@pytest.mark.parametrize(
    ("old_status", "expected"),
    [
        (None, ("joined", "joined", "joined", "ignored", "ignored", "ignored")),
        ("left", ("joined", "joined", "joined", "ignored", "ignored", "ignored")),
        ("kicked", ("joined", "joined", "joined", "ignored", "ignored", "ignored")),
        ("member", ("ignored", "ignored", "ignored", "left", "left", "ignored")),
        ("restricted", ("ignored", "ignored", "ignored", "left", "left", "ignored")),
        ("administrator", ("ignored", "ignored", "ignored", "left", "left", "ignored")),
        ("creator", ("ignored",) * 6),
    ],
)
@pytest.mark.parametrize(
    ("index", "new_status"),
    list(
        enumerate(
            ("member", "restricted", "administrator", "left", "kicked", "creator")
        )
    ),
)
def test_transition_table(old_status, expected, index, new_status):
    assert classify_transition(old_status, new_status) == expected[index]
