"""Pytest configuration and fixtures."""

import pytest


@pytest.fixture
def sample_text():
    """Sample text for testing."""
    return "Hello, world! This is a test message."


@pytest.fixture
def sample_stopwords():
    """Sample stopwords list."""
    return ["spam", "bad", "evil", "hate"]


@pytest.fixture
def chat_id():
    """Sample chat ID."""
    return 123456789


@pytest.fixture
def user_id():
    """Sample user ID."""
    return 987654321


@pytest.fixture
def admin_id():
    """Sample admin ID."""
    return 111222333
