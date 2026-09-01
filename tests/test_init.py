"""Tests for config-entry lifecycle ownership."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.logfire import async_setup_entry, async_unload_entry
from custom_components.logfire.client import InvalidAuthError, ProjectInfo
from custom_components.logfire.const import (
    CONF_ENVIRONMENT,
    CONF_SERVICE_NAME,
    CONF_TOKEN,
    DOMAIN,
)


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_TOKEN: "pylf_v1_us_testtoken",
            CONF_SERVICE_NAME: "home-assistant",
            CONF_ENVIRONMENT: "test",
        },
        options={},
    )


async def test_setup_and_unload_own_private_providers(hass: HomeAssistant) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    client = MagicMock()
    client.meter.create_counter.return_value = MagicMock()
    client.meter.create_gauge.return_value = MagicMock()
    project = ProjectInfo(
        name="home-assistant",
        url="https://logfire.pydantic.dev/example/home-assistant",
    )

    with (
        patch(
            "custom_components.logfire.async_validate_token",
            new=AsyncMock(return_value=project),
        ),
        patch(
            "custom_components.logfire.instance_id.async_get",
            new=AsyncMock(return_value="test-instance"),
        ),
        patch("custom_components.logfire.LogfireOtelClient", return_value=client),
    ):
        assert await async_setup_entry(hass, entry)

    assert entry.runtime_data.client is client
    assert entry.runtime_data.pipeline.diagnostics()["queue_capacity"] == 2048

    assert await async_unload_entry(hass, entry)
    client.shutdown.assert_called_once_with()


async def test_setup_starts_reauth_for_rejected_token(hass: HomeAssistant) -> None:
    entry = _entry()
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.logfire.async_validate_token",
            new=AsyncMock(side_effect=InvalidAuthError),
        ),
        pytest.raises(ConfigEntryAuthFailed),
    ):
        await async_setup_entry(hass, entry)
