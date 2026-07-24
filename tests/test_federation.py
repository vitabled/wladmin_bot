"""Unit tests for the federation name service (Phase 8)."""

from __future__ import annotations

from bot.services.federation import FederationService


class TestNormalizeName:
    def test_trims_and_collapses(self):
        assert FederationService.normalize_name("  My   Fed  ") == "My Fed"

    def test_drops_disallowed_chars(self):
        assert FederationService.normalize_name("Fed!!@#") == "Fed"

    def test_keeps_dash_underscore(self):
        assert FederationService.normalize_name("cool-fed_1") == "cool-fed_1"


class TestIsValidName:
    def test_valid(self):
        assert FederationService.is_valid_name("MyFed")

    def test_too_short(self):
        assert not FederationService.is_valid_name("ab")

    def test_too_long(self):
        assert not FederationService.is_valid_name("x" * 33)

    def test_only_symbols_invalid(self):
        assert not FederationService.is_valid_name("!!!")
