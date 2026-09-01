"""Tests for diagnostics redaction."""

from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.logfire.const import CONF_TOKEN, DOMAIN
from custom_components.logfire.diagnostics import async_get_config_entry_diagnostics


async def test_diagnostics_never_include_write_token(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_TOKEN: "pylf_v1_us_private"},
        options={},
    )
    entry.runtime_data = MagicMock()
    entry.runtime_data.pipeline.diagnostics.return_value = {"queue_size": 0}

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["config_entry"]["data"][CONF_TOKEN] != "pylf_v1_us_private"
    assert "pylf_v1_us_private" not in str(diagnostics)
    assert diagnostics["pipeline"] == {"queue_size": 0}
