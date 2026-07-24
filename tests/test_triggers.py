"""Unit tests for the trigger-matching pure service (Phase 3)."""

from __future__ import annotations

from bot.services.triggers import TriggerService


class TestMatches:
    def test_contains(self):
        assert TriggerService.matches("well hello there", "hello", "contains")

    def test_contains_no_match(self):
        assert not TriggerService.matches("goodbye", "hello", "contains")

    def test_exact(self):
        assert TriggerService.matches("Hello", "hello", "exact")
        assert not TriggerService.matches("hello there", "hello", "exact")

    def test_starts(self):
        assert TriggerService.matches("hello there", "hello", "starts")
        assert not TriggerService.matches("well hello", "hello", "starts")

    def test_case_and_whitespace_insensitive(self):
        assert TriggerService.matches("  HELLO   world ", "hello world", "contains")

    def test_unknown_type_defaults_to_contains(self):
        assert TriggerService.matches("say hello", "hello", "regex")

    def test_empty_text_or_pattern(self):
        assert not TriggerService.matches("", "hello", "contains")
        assert not TriggerService.matches("hello", "", "contains")


class TestFindReply:
    TRIGGERS = [
        {"pattern": "hi", "match_type": "exact", "reply_text": "Hello!"},
        {"pattern": "buy", "match_type": "contains", "reply_text": "No ads."},
    ]

    def test_first_match_wins(self):
        assert TriggerService.find_reply("hi", self.TRIGGERS) == "Hello!"

    def test_contains_match(self):
        assert (
            TriggerService.find_reply("i want to buy now", self.TRIGGERS) == "No ads."
        )

    def test_no_match(self):
        assert TriggerService.find_reply("random text", self.TRIGGERS) is None

    def test_empty_reply_ignored(self):
        triggers = [{"pattern": "x", "match_type": "contains", "reply_text": ""}]
        assert TriggerService.find_reply("x", triggers) is None
