"""Unit tests for the anti-flood / newbie-media pure service (Phase 2)."""

from __future__ import annotations

from bot.services.antiflood import AntifloodService


class TestIsFlood:
    def test_below_limit(self):
        assert not AntifloodService.is_flood(4, 5)

    def test_at_limit(self):
        assert AntifloodService.is_flood(5, 5)

    def test_above_limit(self):
        assert AntifloodService.is_flood(6, 5)

    def test_zero_limit_disabled(self):
        # A misconfigured 0 limit must not flag the very first message.
        assert not AntifloodService.is_flood(1, 0)

    def test_negative_limit_disabled(self):
        assert not AntifloodService.is_flood(10, -1)


class TestIsRestrictedMedia:
    def test_photo_restricted(self):
        assert AntifloodService.is_restricted_media("photo")

    def test_sticker_restricted(self):
        assert AntifloodService.is_restricted_media("sticker")

    def test_text_allowed(self):
        assert not AntifloodService.is_restricted_media("text")

    def test_unknown_allowed(self):
        assert not AntifloodService.is_restricted_media("new_chat_members")

    def test_custom_restricted_set(self):
        assert AntifloodService.is_restricted_media("foo", frozenset({"foo"}))
        assert not AntifloodService.is_restricted_media("photo", frozenset({"foo"}))
