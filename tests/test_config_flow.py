"""Tests for config-entry setup and token handling."""

from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant

from custom_components.logfire.client import InvalidAuthError, ProjectInfo
from custom_components.logfire.const import (
    CONF_ENVIRONMENT,
    CONF_PROJECT_NAME,
    CONF_SERVICE_NAME,
    CONF_TOKEN,
    DOMAIN,
)

USER_INPUT = {
    CONF_TOKEN: "pylf_v1_us_testtoken",
    CONF_SERVICE_NAME: "home-assistant",
    CONF_ENVIRONMENT: "home",
}


async def test_user_flow_creates_entry(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )

    assert result["type"] == "form"
    assert result["step_id"] == "user"

    project = ProjectInfo(
        name="home-assistant",
        url="https://logfire.pydantic.dev/example/home-assistant",
    )
    with (
        patch(
            "custom_components.logfire.config_flow._validate_input",
            new=AsyncMock(return_value=project),
        ),
        patch(
            "custom_components.logfire.async_setup_entry",
            new=AsyncMock(return_value=True),
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            USER_INPUT,
        )
        await hass.async_block_till_done()

    assert result["type"] == "create_entry"
    assert result["title"] == "Logfire: home-assistant"
    assert result["data"][CONF_TOKEN] == USER_INPUT[CONF_TOKEN]
    assert result["data"][CONF_PROJECT_NAME] == "home-assistant"


async def test_user_flow_rejects_invalid_token(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )

    with patch(
        "custom_components.logfire.config_flow._validate_input",
        new=AsyncMock(side_effect=InvalidAuthError),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            USER_INPUT,
        )

    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_auth"}
