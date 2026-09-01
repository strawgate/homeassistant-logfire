"""Diagnostics for Home Assistant Logfire."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import LogfireConfigEntry
from .const import TOKEN_REDACT_KEYS


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: LogfireConfigEntry,
) -> dict[str, Any]:
    """Return redacted configuration and delivery health."""
    return {
        "config_entry": async_redact_data(entry.as_dict(), TOKEN_REDACT_KEYS),
        "pipeline": entry.runtime_data.pipeline.diagnostics(),
    }
