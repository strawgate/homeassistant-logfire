"""Proof that the exporter core has no Home Assistant runtime dependency."""

from __future__ import annotations

import importlib.util
import sys

import custom_components.logfire.core as core


def test_core_imports_without_homeassistant_installed() -> None:
    assert importlib.util.find_spec("homeassistant") is None
    assert "homeassistant" not in sys.modules
    assert core.EventSettings is not None
