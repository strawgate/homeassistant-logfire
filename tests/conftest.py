"""Shared fixtures for Home Assistant Logfire tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow the custom integration to load in Home Assistant tests."""
    return enable_custom_integrations
