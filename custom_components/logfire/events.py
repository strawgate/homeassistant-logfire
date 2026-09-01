"""Minimize Home Assistant events into a stable telemetry schema."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Final

from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_UNIT_OF_MEASUREMENT,
    EVENT_CALL_SERVICE,
    EVENT_STATE_CHANGED,
)
from homeassistant.core import Event, HomeAssistant, State
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from opentelemetry._logs import SeverityNumber

from .client import TelemetryRecord
from .const import EVENT_AUTOMATION_TRIGGERED, EVENT_SYSTEM_LOG

_EXPORTER_LOGGER_PREFIXES: Final = (
    "custom_components.logfire",
    "opentelemetry",
)
_MAX_MESSAGE_LENGTH: Final = 4096
_MAX_STATE_LENGTH: Final = 1024
_MAX_TARGETS: Final = 50
_BEARER_PATTERN: Final = re.compile(r"(?i)\bBearer\s+[^\s,;]+")
_LOGFIRE_TOKEN_PATTERN: Final = re.compile(r"pylf_v[0-9]+_[a-z]+_[A-Za-z0-9_-]+")
_SECRET_VALUE_PATTERN: Final = re.compile(
    r"(?i)\b(authorization|token|password|api[_-]?key)\b(\s*[:=]\s*)([^\s,;]+)"
)


@dataclass(frozen=True, slots=True)
class EventSettings:
    """Event groups and entity filters applied before serialization."""

    export_automations: bool
    export_service_calls: bool
    export_state_changes: bool
    export_system_logs: bool
    include_domains: frozenset[str]
    exclude_entities: frozenset[str]

    def accepts_entity(self, entity_id: str) -> bool:
        """Return whether an entity passes configured filters."""
        if entity_id in self.exclude_entities:
            return False
        domain = entity_id.partition(".")[0]
        return not self.include_domains or domain in self.include_domains


def _bounded(value: Any, limit: int) -> str:
    return str(value)[:limit]


def _redact_message(value: str) -> str:
    value = _LOGFIRE_TOKEN_PATTERN.sub("<redacted>", value)
    value = _BEARER_PATTERN.sub("Bearer <redacted>", value)
    return _SECRET_VALUE_PATTERN.sub(r"\1\2<redacted>", value)


def _context_attributes(event: Event[Any]) -> dict[str, str]:
    attributes: dict[str, str] = {}
    if event.context.id:
        attributes["homeassistant.context.id"] = event.context.id
    if event.context.parent_id:
        attributes["homeassistant.context.parent_id"] = event.context.parent_id
    return attributes


def _entity_attributes(hass: HomeAssistant, entity_id: str) -> dict[str, Any]:
    attributes: dict[str, Any] = {
        "homeassistant.entity.domain": entity_id.partition(".")[0],
        "homeassistant.entity.id": entity_id,
    }
    entity = er.async_get(hass).async_get(entity_id)
    if entity is None:
        return attributes
    if entity.device_id:
        attributes["homeassistant.device.id"] = entity.device_id
    area_id = entity.area_id
    if area_id is None and entity.device_id:
        device = dr.async_get(hass).async_get(entity.device_id)
        if device is not None:
            area_id = device.area_id
    if area_id:
        attributes["homeassistant.area.id"] = area_id
    if entity.labels:
        attributes["homeassistant.label.ids"] = tuple(sorted(entity.labels))
    return attributes


def _state_attributes(state: State | None, prefix: str) -> dict[str, Any]:
    if state is None:
        return {}
    attributes: dict[str, Any] = {
        f"homeassistant.state.{prefix}": _bounded(state.state, _MAX_STATE_LENGTH),
    }
    if unit := state.attributes.get(ATTR_UNIT_OF_MEASUREMENT):
        attributes["homeassistant.entity.unit"] = _bounded(unit, 64)
    if device_class := state.attributes.get(ATTR_DEVICE_CLASS):
        attributes["homeassistant.entity.device_class"] = _bounded(device_class, 64)
    if state_class := state.attributes.get("state_class"):
        attributes["homeassistant.entity.state_class"] = _bounded(state_class, 64)
    return attributes


def _normalize_entity_ids(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        values: Iterable[Any] = (value,)
    elif isinstance(value, (list, tuple, set)):
        values = value
    else:
        return ()
    return tuple(item for item in values if isinstance(item, str))[:_MAX_TARGETS]


def _state_changed_record(
    hass: HomeAssistant,
    event: Event[Any],
    settings: EventSettings,
) -> TelemetryRecord | None:
    entity_id = event.data.get("entity_id")
    if not isinstance(entity_id, str) or not settings.accepts_entity(entity_id):
        return None
    old_state = event.data.get("old_state")
    new_state = event.data.get("new_state")
    attributes = {
        "event.name": "homeassistant.state_changed",
        **_entity_attributes(hass, entity_id),
        **_state_attributes(old_state if isinstance(old_state, State) else None, "old"),
        **_state_attributes(new_state if isinstance(new_state, State) else None, "new"),
        **_context_attributes(event),
    }
    return _record(event, "homeassistant.state_changed", "Home Assistant state changed", attributes)


def _service_call_record(
    event: Event[Any],
    settings: EventSettings,
) -> TelemetryRecord | None:
    domain = event.data.get("domain")
    service = event.data.get("service")
    if not isinstance(domain, str) or not isinstance(service, str):
        return None
    service_data = event.data.get("service_data")
    entity_ids = ()
    if isinstance(service_data, dict):
        entity_ids = _normalize_entity_ids(service_data.get("entity_id"))
    filtered_entity_ids = tuple(
        entity_id for entity_id in entity_ids if settings.accepts_entity(entity_id)
    )
    if entity_ids and not filtered_entity_ids:
        return None
    attributes: dict[str, Any] = {
        "event.name": "homeassistant.service.called",
        "homeassistant.service.domain": _bounded(domain, 128),
        "homeassistant.service.name": _bounded(service, 128),
        **_context_attributes(event),
    }
    if filtered_entity_ids:
        attributes["homeassistant.target.entity.ids"] = filtered_entity_ids
    return _record(
        event,
        "homeassistant.service.called",
        "Home Assistant service called",
        attributes,
    )


def _automation_record(
    event: Event[Any],
    settings: EventSettings,
) -> TelemetryRecord | None:
    entity_id = event.data.get("entity_id")
    if not isinstance(entity_id, str) or not settings.accepts_entity(entity_id):
        return None
    attributes = {
        "event.name": "homeassistant.automation.triggered",
        "homeassistant.automation.id": entity_id,
        **_context_attributes(event),
    }
    if name := event.data.get("name"):
        attributes["homeassistant.automation.name"] = _bounded(name, 256)
    return _record(
        event,
        "homeassistant.automation.triggered",
        "Home Assistant automation triggered",
        attributes,
    )


def _system_log_record(event: Event[Any]) -> TelemetryRecord | None:
    logger_name = event.data.get("name", "")
    if not isinstance(logger_name, str) or logger_name.startswith(_EXPORTER_LOGGER_PREFIXES):
        return None
    level = str(event.data.get("level", "WARNING")).upper()
    severity_number = {
        "DEBUG": SeverityNumber.DEBUG,
        "ERROR": SeverityNumber.ERROR,
        "CRITICAL": SeverityNumber.FATAL,
        "INFO": SeverityNumber.INFO,
        "WARNING": SeverityNumber.WARN,
    }.get(level, SeverityNumber.WARN)
    message_data = event.data.get("message", "")
    if isinstance(message_data, (list, tuple)):
        message = " ".join(str(part) for part in message_data)
    else:
        message = str(message_data)
    attributes = {
        "event.name": "homeassistant.system_log",
        "homeassistant.logger.name": _bounded(logger_name, 256),
        "homeassistant.log.source": _bounded(event.data.get("source", ""), 512),
        **_context_attributes(event),
    }
    return _record(
        event,
        "homeassistant.system_log",
        _redact_message(_bounded(message, _MAX_MESSAGE_LENGTH)),
        attributes,
        severity_number=severity_number,
        severity_text=level,
    )


def _record(
    event: Event[Any],
    event_name: str,
    body: str,
    attributes: dict[str, Any],
    *,
    severity_number: SeverityNumber = SeverityNumber.INFO,
    severity_text: str = "INFO",
) -> TelemetryRecord:
    return TelemetryRecord(
        event_name=event_name,
        body=body,
        attributes=attributes,
        timestamp_ns=int(event.time_fired.timestamp() * 1_000_000_000),
        severity_number=severity_number,
        severity_text=severity_text,
    )


def build_record(
    hass: HomeAssistant,
    event: Event[Any],
    settings: EventSettings,
) -> TelemetryRecord | None:
    """Build a minimized record for a subscribed Home Assistant event."""
    if event.event_type == EVENT_STATE_CHANGED and settings.export_state_changes:
        return _state_changed_record(hass, event, settings)
    if event.event_type == EVENT_CALL_SERVICE and settings.export_service_calls:
        return _service_call_record(event, settings)
    if event.event_type == EVENT_AUTOMATION_TRIGGERED and settings.export_automations:
        return _automation_record(event, settings)
    if event.event_type == EVENT_SYSTEM_LOG and settings.export_system_logs:
        return _system_log_record(event)
    return _record(
        event,
        f"homeassistant.{event.event_type}",
        f"Home Assistant lifecycle: {event.event_type}",
        {
            "event.name": f"homeassistant.{event.event_type}",
            **_context_attributes(event),
        },
    )
