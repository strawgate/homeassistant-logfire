"""Bounded event delivery and event-loop-safe health metrics."""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    EVENT_CALL_SERVICE,
    EVENT_HOMEASSISTANT_START,
    EVENT_HOMEASSISTANT_STARTED,
    EVENT_HOMEASSISTANT_STOP,
    EVENT_STATE_CHANGED,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.setup import async_get_setup_timings
from opentelemetry.metrics import Counter as OtelCounter

from .client import LogfireOtelClient, TelemetryRecord
from .const import (
    CONF_EXCLUDE_ENTITIES,
    CONF_EXPORT_AUTOMATIONS,
    CONF_EXPORT_METRICS,
    CONF_EXPORT_SERVICE_CALLS,
    CONF_EXPORT_STATE_CHANGES,
    CONF_EXPORT_SYSTEM_LOGS,
    CONF_INCLUDE_DOMAINS,
    CONF_METRIC_INTERVAL,
    CONF_QUEUE_SIZE,
    DEFAULT_EXPORT_AUTOMATIONS,
    DEFAULT_EXPORT_METRICS,
    DEFAULT_EXPORT_SERVICE_CALLS,
    DEFAULT_EXPORT_STATE_CHANGES,
    DEFAULT_EXPORT_SYSTEM_LOGS,
    DEFAULT_METRIC_INTERVAL,
    DEFAULT_QUEUE_SIZE,
    EVENT_AUTOMATION_TRIGGERED,
    EVENT_SYSTEM_LOG,
)
from .events import EventSettings, build_record

_LOGGER = logging.getLogger(__name__)


class Gauge(Protocol):
    """Subset of the provisional OpenTelemetry synchronous gauge API we use."""

    def set(self, amount: int | float, attributes: dict[str, str]) -> None:
        """Record the current value for one attribute set."""


@dataclass(frozen=True, slots=True)
class PipelineSettings:
    """Validated runtime settings derived from config-entry options."""

    events: EventSettings
    export_metrics: bool
    metric_interval: int
    queue_size: int

    @classmethod
    def from_entry(cls, entry: ConfigEntry) -> PipelineSettings:
        """Build runtime settings with defaults."""
        options = entry.options
        return cls(
            events=EventSettings(
                export_automations=options.get(
                    CONF_EXPORT_AUTOMATIONS,
                    DEFAULT_EXPORT_AUTOMATIONS,
                ),
                export_service_calls=options.get(
                    CONF_EXPORT_SERVICE_CALLS,
                    DEFAULT_EXPORT_SERVICE_CALLS,
                ),
                export_state_changes=options.get(
                    CONF_EXPORT_STATE_CHANGES,
                    DEFAULT_EXPORT_STATE_CHANGES,
                ),
                export_system_logs=options.get(
                    CONF_EXPORT_SYSTEM_LOGS,
                    DEFAULT_EXPORT_SYSTEM_LOGS,
                ),
                include_domains=frozenset(options.get(CONF_INCLUDE_DOMAINS, [])),
                exclude_entities=frozenset(options.get(CONF_EXCLUDE_ENTITIES, [])),
            ),
            export_metrics=options.get(CONF_EXPORT_METRICS, DEFAULT_EXPORT_METRICS),
            metric_interval=int(options.get(CONF_METRIC_INTERVAL, DEFAULT_METRIC_INTERVAL)),
            queue_size=int(options.get(CONF_QUEUE_SIZE, DEFAULT_QUEUE_SIZE)),
        )


@dataclass(slots=True)
class PipelineStats:
    """Non-sensitive runtime state exposed through diagnostics."""

    dropped: int = 0
    emitted: int = 0
    enqueued: int = 0
    failed: int = 0
    last_error_at: str | None = None
    last_emit_at: str | None = None


class TelemetryPipeline:
    """Decouple Home Assistant callbacks from OTLP delivery."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: LogfireOtelClient,
        settings: PipelineSettings,
    ) -> None:
        """Initialize queue, instruments, and lifecycle-owned handles."""
        self._hass = hass
        self._entry = entry
        self._client = client
        self.settings = settings
        self.stats = PipelineStats()
        self._queue: asyncio.Queue[TelemetryRecord] = asyncio.Queue(settings.queue_size)
        self._worker_task: asyncio.Task[None] | None = None
        self._remove_listeners: list[Callable[[], None]] = []
        self._remove_metric_interval: Callable[[], None] | None = None

        meter = client.meter
        self._events_counter: OtelCounter = meter.create_counter(
            "homeassistant.telemetry.event",
            unit="1",
            description="Home Assistant telemetry records accepted for export",
        )
        self._dropped_counter: OtelCounter = meter.create_counter(
            "homeassistant.telemetry.dropped",
            unit="1",
            description="Home Assistant telemetry records dropped before export",
        )
        self._entity_count: Gauge = meter.create_gauge(
            "homeassistant.entity.count",
            unit="1",
            description="Entities known to Home Assistant",
        )
        self._unavailable_count: Gauge = meter.create_gauge(
            "homeassistant.entity.unavailable.count",
            unit="1",
            description="Entities in unavailable or unknown state",
        )
        self._config_entry_count: Gauge = meter.create_gauge(
            "homeassistant.config_entry.count",
            unit="1",
            description="Home Assistant config entries by domain and state",
        )
        self._setup_duration: Gauge = meter.create_gauge(
            "homeassistant.integration.setup.duration",
            unit="s",
            description="Home Assistant integration setup duration",
        )
        self._previous_metric_keys: dict[str, set[tuple[tuple[str, str], ...]]] = {}

    async def async_start(self) -> None:
        """Start listeners, worker, and metric sampling."""
        event_types = {
            EVENT_HOMEASSISTANT_START,
            EVENT_HOMEASSISTANT_STARTED,
            EVENT_HOMEASSISTANT_STOP,
        }
        if self.settings.events.export_state_changes:
            event_types.add(EVENT_STATE_CHANGED)
        if self.settings.events.export_service_calls:
            event_types.add(EVENT_CALL_SERVICE)
        if self.settings.events.export_automations:
            event_types.add(EVENT_AUTOMATION_TRIGGERED)
        if self.settings.events.export_system_logs:
            event_types.add(EVENT_SYSTEM_LOG)
        for event_type in event_types:
            self._remove_listeners.append(
                self._hass.bus.async_listen(event_type, self._async_handle_event)
            )

        self._worker_task = self._entry.async_create_background_task(
            self._hass,
            self._async_worker(),
            "Logfire telemetry worker",
        )
        if self.settings.export_metrics:
            self._collect_metrics()
            self._remove_metric_interval = async_track_time_interval(
                self._hass,
                self._async_collect_metrics,
                timedelta(seconds=self.settings.metric_interval),
                name="Logfire health metrics",
            )

    async def async_stop(self) -> None:
        """Remove inputs and best-effort drain the application queue."""
        for remove_listener in self._remove_listeners:
            remove_listener()
        self._remove_listeners.clear()
        if self._remove_metric_interval is not None:
            self._remove_metric_interval()
            self._remove_metric_interval = None

        try:
            async with asyncio.timeout(2):
                await self._queue.join()
        except TimeoutError:
            _LOGGER.warning(
                "Timed out draining %s Home Assistant telemetry records",
                self._queue.qsize(),
            )
        if self._worker_task is not None:
            self._worker_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._worker_task
            self._worker_task = None

    @callback
    def _async_handle_event(self, event: Event[Any]) -> None:
        record = build_record(self._hass, event, self.settings.events)
        if record is None:
            return
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            self.stats.dropped += 1
            self._dropped_counter.add(1, {"reason": "application_queue_full"})
            return
        self.stats.enqueued += 1

    async def _async_worker(self) -> None:
        while True:
            record = await self._queue.get()
            try:
                self._client.emit(record)
                self.stats.emitted += 1
                self.stats.last_emit_at = datetime.now().astimezone().isoformat()
                self._events_counter.add(1, {"event.name": record.event_name})
            except Exception:
                self.stats.failed += 1
                self.stats.last_error_at = datetime.now().astimezone().isoformat()
                self._dropped_counter.add(1, {"reason": "sdk_emit_error"})
                _LOGGER.exception("Failed to emit a Home Assistant telemetry record")
            finally:
                self._queue.task_done()

    @callback
    def _async_collect_metrics(self, now: datetime) -> None:
        self._collect_metrics()

    def _set_gauge_values(
        self,
        name: str,
        gauge: Gauge,
        values: dict[tuple[tuple[str, str], ...], float],
    ) -> None:
        current_keys = set(values)
        for attributes in self._previous_metric_keys.get(name, set()) - current_keys:
            gauge.set(0, dict(attributes))
        for attributes, value in values.items():
            gauge.set(value, dict(attributes))
        self._previous_metric_keys[name] = current_keys

    def _collect_metrics(self) -> None:
        entity_counts: Counter[str] = Counter()
        unavailable_counts: Counter[str] = Counter()
        for state in self._hass.states.async_all():
            if not self.settings.events.accepts_entity(state.entity_id):
                continue
            entity_counts[state.domain] += 1
            if state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                unavailable_counts[state.domain] += 1

        self._set_gauge_values(
            "entity_count",
            self._entity_count,
            {(("homeassistant.domain", domain),): count for domain, count in entity_counts.items()},
        )
        self._set_gauge_values(
            "unavailable_count",
            self._unavailable_count,
            {
                (("homeassistant.domain", domain),): count
                for domain, count in unavailable_counts.items()
            },
        )

        config_entry_counts: Counter[tuple[str, str]] = Counter(
            (config_entry.domain, config_entry.state.value)
            for config_entry in self._hass.config_entries.async_entries()
        )
        self._set_gauge_values(
            "config_entry_count",
            self._config_entry_count,
            {
                (
                    ("homeassistant.domain", domain),
                    ("homeassistant.config_entry.state", state),
                ): count
                for (domain, state), count in config_entry_counts.items()
            },
        )
        self._set_gauge_values(
            "setup_duration",
            self._setup_duration,
            {
                (("homeassistant.domain", domain),): duration
                for domain, duration in async_get_setup_timings(self._hass).items()
            },
        )

    def diagnostics(self) -> dict[str, Any]:
        """Return non-sensitive runtime diagnostics."""
        return {
            "dropped": self.stats.dropped,
            "emitted": self.stats.emitted,
            "enqueued": self.stats.enqueued,
            "failed": self.stats.failed,
            "last_error_at": self.stats.last_error_at,
            "last_emit_at": self.stats.last_emit_at,
            "queue_capacity": self.settings.queue_size,
            "queue_size": self._queue.qsize(),
        }
