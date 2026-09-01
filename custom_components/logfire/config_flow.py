"""Config flow for Home Assistant Logfire."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import CannotConnectError, InvalidAuthError, ProjectInfo, async_validate_token
from .const import (
    CONF_ENVIRONMENT,
    CONF_EXCLUDE_ENTITIES,
    CONF_EXPORT_AUTOMATIONS,
    CONF_EXPORT_METRICS,
    CONF_EXPORT_SERVICE_CALLS,
    CONF_EXPORT_STATE_CHANGES,
    CONF_EXPORT_SYSTEM_LOGS,
    CONF_INCLUDE_DOMAINS,
    CONF_METRIC_INTERVAL,
    CONF_PROJECT_NAME,
    CONF_PROJECT_URL,
    CONF_QUEUE_SIZE,
    CONF_SERVICE_NAME,
    CONF_TOKEN,
    DEFAULT_ENVIRONMENT,
    DEFAULT_EXPORT_AUTOMATIONS,
    DEFAULT_EXPORT_METRICS,
    DEFAULT_EXPORT_SERVICE_CALLS,
    DEFAULT_EXPORT_STATE_CHANGES,
    DEFAULT_EXPORT_SYSTEM_LOGS,
    DEFAULT_METRIC_INTERVAL,
    DEFAULT_QUEUE_SIZE,
    DEFAULT_SERVICE_NAME,
    DOMAIN,
)


def _connection_schema(*, token_required: bool) -> vol.Schema:
    token_key = vol.Required(CONF_TOKEN) if token_required else vol.Optional(CONF_TOKEN)
    return vol.Schema(
        {
            token_key: selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
            vol.Required(CONF_SERVICE_NAME, default=DEFAULT_SERVICE_NAME): selector.TextSelector(),
            vol.Required(CONF_ENVIRONMENT, default=DEFAULT_ENVIRONMENT): selector.TextSelector(),
        }
    )


async def _validate_input(flow: ConfigFlow, token: str) -> ProjectInfo:
    return await async_validate_token(async_get_clientsession(flow.hass), token)


class LogfireConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle Logfire configuration."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return LogfireOptionsFlow(config_entry)

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Configure a Logfire project write token."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                project = await _validate_input(self, user_input[CONF_TOKEN])
            except InvalidAuthError:
                errors["base"] = "invalid_auth"
            except CannotConnectError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(project.url)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Logfire: {project.name}",
                    data={
                        **user_input,
                        CONF_PROJECT_NAME: project.name,
                        CONF_PROJECT_URL: project.url,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                _connection_schema(token_required=True),
                user_input or {},
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self,
        entry_data: dict[str, Any],
    ) -> ConfigFlowResult:
        """Start token reauthentication."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Replace a rejected write token."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                project = await _validate_input(self, user_input[CONF_TOKEN])
            except InvalidAuthError:
                errors["base"] = "invalid_auth"
            except CannotConnectError:
                errors["base"] = "cannot_connect"
            else:
                entry = self._get_reauth_entry()
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_TOKEN: user_input[CONF_TOKEN],
                        CONF_PROJECT_NAME: project.name,
                        CONF_PROJECT_URL: project.url,
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_TOKEN): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                )
            }
        )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_reconfigure(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Update connection metadata and optionally rotate the token."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input.get(CONF_TOKEN) or entry.data[CONF_TOKEN]
            try:
                project = await _validate_input(self, token)
            except InvalidAuthError:
                errors["base"] = "invalid_auth"
            except CannotConnectError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_TOKEN: token,
                        CONF_SERVICE_NAME: user_input[CONF_SERVICE_NAME],
                        CONF_ENVIRONMENT: user_input[CONF_ENVIRONMENT],
                        CONF_PROJECT_NAME: project.name,
                        CONF_PROJECT_URL: project.url,
                    },
                )

        suggested = {
            CONF_SERVICE_NAME: entry.data[CONF_SERVICE_NAME],
            CONF_ENVIRONMENT: entry.data[CONF_ENVIRONMENT],
            **(user_input or {}),
        }
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                _connection_schema(token_required=False),
                suggested,
            ),
            errors=errors,
        )


class LogfireOptionsFlow(OptionsFlow):
    """Configure telemetry selection and queue bounds."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options from a config entry."""
        self._config_entry = config_entry

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Manage telemetry options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self._config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_EXPORT_STATE_CHANGES,
                    default=current.get(
                        CONF_EXPORT_STATE_CHANGES,
                        DEFAULT_EXPORT_STATE_CHANGES,
                    ),
                ): selector.BooleanSelector(),
                vol.Required(
                    CONF_EXPORT_SERVICE_CALLS,
                    default=current.get(
                        CONF_EXPORT_SERVICE_CALLS,
                        DEFAULT_EXPORT_SERVICE_CALLS,
                    ),
                ): selector.BooleanSelector(),
                vol.Required(
                    CONF_EXPORT_AUTOMATIONS,
                    default=current.get(CONF_EXPORT_AUTOMATIONS, DEFAULT_EXPORT_AUTOMATIONS),
                ): selector.BooleanSelector(),
                vol.Required(
                    CONF_EXPORT_SYSTEM_LOGS,
                    default=current.get(CONF_EXPORT_SYSTEM_LOGS, DEFAULT_EXPORT_SYSTEM_LOGS),
                ): selector.BooleanSelector(),
                vol.Required(
                    CONF_EXPORT_METRICS,
                    default=current.get(CONF_EXPORT_METRICS, DEFAULT_EXPORT_METRICS),
                ): selector.BooleanSelector(),
                vol.Optional(
                    CONF_INCLUDE_DOMAINS,
                    default=current.get(CONF_INCLUDE_DOMAINS, []),
                ): selector.TextSelector(selector.TextSelectorConfig(multiple=True)),
                vol.Optional(
                    CONF_EXCLUDE_ENTITIES,
                    default=current.get(CONF_EXCLUDE_ENTITIES, []),
                ): selector.EntitySelector(selector.EntitySelectorConfig(multiple=True)),
                vol.Required(
                    CONF_METRIC_INTERVAL,
                    default=current.get(CONF_METRIC_INTERVAL, DEFAULT_METRIC_INTERVAL),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=30,
                        max=900,
                        step=30,
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_QUEUE_SIZE,
                    default=current.get(CONF_QUEUE_SIZE, DEFAULT_QUEUE_SIZE),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=256,
                        max=8192,
                        step=256,
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
