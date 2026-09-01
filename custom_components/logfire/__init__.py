"""Export Home Assistant telemetry directly to Pydantic Logfire."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .core.otlp import LogfireOtelClient
    from .pipeline import TelemetryPipeline


@dataclass(slots=True)
class LogfireRuntimeData:
    """Runtime objects owned by one config entry."""

    client: LogfireOtelClient
    pipeline: TelemetryPipeline


if TYPE_CHECKING:
    type LogfireConfigEntry = ConfigEntry[LogfireRuntimeData]
else:
    LogfireConfigEntry = Any


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the integration namespace."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: LogfireConfigEntry) -> bool:
    """Set up a Logfire config entry."""
    from homeassistant import const as ha_const
    from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
    from homeassistant.helpers import instance_id
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    from .client import (
        CannotConnectError,
        InvalidAuthError,
        LogfireOtelClient,
        async_validate_token,
    )
    from .const import (
        CONF_ENVIRONMENT,
        CONF_SERVICE_NAME,
        CONF_TOKEN,
        DEFAULT_ENVIRONMENT,
        DEFAULT_SERVICE_NAME,
    )
    from .pipeline import PipelineSettings, TelemetryPipeline

    token = entry.data[CONF_TOKEN]
    try:
        await async_validate_token(async_get_clientsession(hass), token)
    except InvalidAuthError as error:
        raise ConfigEntryAuthFailed("Logfire rejected the project write token") from error
    except CannotConnectError as error:
        raise ConfigEntryNotReady("Unable to reach the Logfire API") from error

    settings = PipelineSettings.from_entry(entry)
    service_instance_id = await instance_id.async_get(hass)
    try:
        client = await hass.async_add_executor_job(
            partial(
                LogfireOtelClient,
                token=token,
                service_name=entry.data.get(CONF_SERVICE_NAME, DEFAULT_SERVICE_NAME),
                service_version=ha_const.__version__,
                service_instance_id=service_instance_id,
                environment=entry.data.get(CONF_ENVIRONMENT, DEFAULT_ENVIRONMENT),
                metric_export_interval=settings.metric_interval,
                queue_size=settings.queue_size,
                export_metrics=settings.export_metrics,
            )
        )
    except Exception as error:
        raise ConfigEntryNotReady("Unable to initialize OpenTelemetry exporters") from error

    pipeline = TelemetryPipeline(hass, entry, client, settings)
    await pipeline.async_start()
    entry.runtime_data = LogfireRuntimeData(client=client, pipeline=pipeline)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LogfireConfigEntry) -> bool:
    """Unload listeners, tasks, and private telemetry providers."""
    runtime = entry.runtime_data
    await runtime.pipeline.async_stop()
    await hass.async_add_executor_job(runtime.client.shutdown)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: LogfireConfigEntry) -> None:
    """Reload when connection data or telemetry options change."""
    await hass.config_entries.async_reload(entry.entry_id)
