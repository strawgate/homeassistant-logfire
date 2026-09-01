"""Shared fixtures for Home Assistant Logfire tests."""

from __future__ import annotations

import importlib.util

import pytest

if importlib.util.find_spec("homeassistant") is not None:

    @pytest.fixture(autouse=True)
    def auto_enable_custom_integrations(enable_custom_integrations):
        """Allow the custom integration to load in Home Assistant tests."""
        return enable_custom_integrations
