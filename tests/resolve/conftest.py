"""Offline fixtures for the resolver tests."""

from __future__ import annotations

import pytest
from resolve_fakes import Clock, FakeDigiKey


@pytest.fixture
def fake():
    return FakeDigiKey()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def cache(tmp_path):
    return tmp_path / "cache"


@pytest.fixture
def env(tmp_path, fake, clock, cache):
    """Keyword arguments for the session API."""
    return {"client": fake, "cache_dir": cache, "clock": clock, "sleep": clock.sleep}
