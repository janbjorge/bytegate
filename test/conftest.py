"""Bytegate test configuration."""

import pytest


@pytest.fixture
def anyio_backend() -> str:
    """Use asyncio as the anyio backend for tests."""
    return "asyncio"
