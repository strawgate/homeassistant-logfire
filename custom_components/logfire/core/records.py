"""Pure event filtering, redaction, and record serialization."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Final

from opentelemetry._logs import SeverityNumber

from .model import (
    AutomationTriggered,
    BaseEvent,
    EntitySnapshot,
    EventSnapshot,
    LifecycleEvent,
    ServiceCalled,
    StateChanged,
    StateSnapshot,
    SystemLogEvent,
    TelemetryRecord,
)

_EXPORTER_LOGGER_PREFIXES: Final = ("custom_components.logfire", "opentelemetry")
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


def _bounded(value: object, limit: int) -> str:
    return str(value)[:limit]


def redact_message(value: str) -> str:
    """Redact common credential shapes from a bounded system-log message."""
    value = _LOGFIRE_TOKEN_PATTERN.sub("<redacted>", value)
    value = _BEARER_PATTERN.sub("Bearer <redacted>", value)
    return _SECRET_VALUE_PATTERN.sub(r"\1\2<redacted>", value)


def _context_attributes(event: BaseEvent) -> dict[str, str]:
    attributes: dict[str, str] = {}
    if event.context.context_id:
        attributes["homeassistant.context.id"] = event.context.context_id
    if event.context.parent_id:
        attributes["homeassistant.context.parent_id"] = event.context.parent_id
    return attributes


def _entity_attributes(entity: EntitySnapshot) -> dict[str, Any]:
    attributes: dict[str, Any] = {
        "homeassistant.entity.domain": entity.domain,
        "homeassistant.entity.id": entity.entity_id,
    }
    if entity.device_id:
        attributes["homeassistant.device.id"] = entity.device_id
    if entity.area_id:
        attributes["homeassistant.area.id"] = entity.area_id
    if entity.label_ids:
        attributes["homeassistant.label.ids"] = entity.label_ids
    return attributes


def _state_attributes(state: StateSnapshot | None, prefix: str) -> dict[str, Any]:
    if state is None:
        return {}
    attributes: dict[str, Any] = {
        f"homeassistant.state.{prefix}": _bounded(state.value, _MAX_STATE_LENGTH),
    }
    if state.unit:
        attributes["homeassistant.entity.unit"] = _bounded(state.unit, 64)
    if state.device_class:
        attributes["homeassistant.entity.device_class"] = _bounded(state.device_class, 64)
    if state.state_class:
        attributes["homeassistant.entity.state_class"] = _bounded(state.state_class, 64)
    return attributes


def _record(
    event: BaseEvent,
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
        timestamp_ns=event.timestamp_ns,
        severity_number=severity_number,
        severity_text=severity_text,
    )


def _serialize_state_changed(
    event: StateChanged,
    settings: EventSettings,
) -> TelemetryRecord | None:
    if not settings.export_state_changes or not settings.accepts_entity(event.entity.entity_id):
        return None
    attributes = {
        "event.name": "homeassistant.state_changed",
        **_entity_attributes(event.entity),
        **_state_attributes(event.old_state, "old"),
        **_state_attributes(event.new_state, "new"),
        **_context_attributes(event),
    }
    return _record(event, "homeassistant.state_changed", "Home Assistant state changed", attributes)


def _serialize_service_call(
    event: ServiceCalled,
    settings: EventSettings,
) -> TelemetryRecord | None:
    if not settings.export_service_calls or not event.domain or not event.service:
        return None
    entity_ids = event.target_entity_ids[:_MAX_TARGETS]
    filtered_entity_ids = tuple(
        entity_id for entity_id in entity_ids if settings.accepts_entity(entity_id)
    )
    if entity_ids and not filtered_entity_ids:
        return None
    attributes: dict[str, Any] = {
        "event.name": "homeassistant.service.called",
        "homeassistant.service.domain": _bounded(event.domain, 128),
        "homeassistant.service.name": _bounded(event.service, 128),
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


def _serialize_automation(
    event: AutomationTriggered,
    settings: EventSettings,
) -> TelemetryRecord | None:
    if (
        not settings.export_automations
        or not event.entity_id
        or not settings.accepts_entity(event.entity_id)
    ):
        return None
    attributes = {
        "event.name": "homeassistant.automation.triggered",
        "homeassistant.automation.id": event.entity_id,
        **_context_attributes(event),
    }
    if event.name:
        attributes["homeassistant.automation.name"] = _bounded(event.name, 256)
    return _record(
        event,
        "homeassistant.automation.triggered",
        "Home Assistant automation triggered",
        attributes,
    )


def _serialize_system_log(
    event: SystemLogEvent,
    settings: EventSettings,
) -> TelemetryRecord | None:
    if (
        not settings.export_system_logs
        or not event.logger_name
        or event.logger_name.startswith(_EXPORTER_LOGGER_PREFIXES)
    ):
        return None
    level = event.level.upper()
    severity_number = {
        "DEBUG": SeverityNumber.DEBUG,
        "ERROR": SeverityNumber.ERROR,
        "CRITICAL": SeverityNumber.FATAL,
        "INFO": SeverityNumber.INFO,
        "WARNING": SeverityNumber.WARN,
    }.get(level, SeverityNumber.WARN)
    return _record(
        event,
        "homeassistant.system_log",
        redact_message(_bounded(event.message, _MAX_MESSAGE_LENGTH)),
        {
            "event.name": "homeassistant.system_log",
            "homeassistant.logger.name": _bounded(event.logger_name, 256),
            "homeassistant.log.source": _bounded(event.source, 512),
            **_context_attributes(event),
        },
        severity_number=severity_number,
        severity_text=level,
    )


def serialize_event(event: EventSnapshot, settings: EventSettings) -> TelemetryRecord | None:
    """Serialize one plain event snapshot into the documented telemetry schema."""
    if isinstance(event, StateChanged):
        return _serialize_state_changed(event, settings)
    if isinstance(event, ServiceCalled):
        return _serialize_service_call(event, settings)
    if isinstance(event, AutomationTriggered):
        return _serialize_automation(event, settings)
    if isinstance(event, SystemLogEvent):
        return _serialize_system_log(event, settings)
    if isinstance(event, LifecycleEvent):
        event_name = f"homeassistant.{event.event_type}"
        return _record(
            event,
            event_name,
            f"Home Assistant lifecycle: {event.event_type}",
            {"event.name": event_name, **_context_attributes(event)},
        )
    raise TypeError(f"Unsupported event snapshot: {type(event).__name__}")
