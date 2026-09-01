"""Adapt Home Assistant events into framework-independent snapshots."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_UNIT_OF_MEASUREMENT,
    EVENT_CALL_SERVICE,
    EVENT_STATE_CHANGED,
)
from homeassistant.core import Event, HomeAssistant, State
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import EVENT_AUTOMATION_TRIGGERED, EVENT_SYSTEM_LOG
from .core.model import (
    AutomationTriggered,
    ContextSnapshot,
    EntitySnapshot,
    EventSnapshot,
    LifecycleEvent,
    ServiceCalled,
    StateChanged,
    StateSnapshot,
    SystemLogEvent,
    TelemetryRecord,
)
from .core.records import EventSettings, serialize_event

__all__ = ["EventSettings", "build_record", "snapshot_event"]


def _context_snapshot(event: Event[Any]) -> ContextSnapshot:
    return ContextSnapshot(
        context_id=event.context.id or None,
        parent_id=event.context.parent_id or None,
    )


def _entity_snapshot(hass: HomeAssistant, entity_id: str) -> EntitySnapshot:
    entity = er.async_get(hass).async_get(entity_id)
    if entity is None:
        return EntitySnapshot(entity_id)
    area_id = entity.area_id
    if area_id is None and entity.device_id:
        device = dr.async_get(hass).async_get(entity.device_id)
        if device is not None:
            area_id = device.area_id
    return EntitySnapshot(
        entity_id=entity_id,
        device_id=entity.device_id,
        area_id=area_id,
        label_ids=tuple(sorted(entity.labels)),
    )


def _state_snapshot(state: State | None) -> StateSnapshot | None:
    if state is None:
        return None
    return StateSnapshot(
        value=state.state,
        unit=state.attributes.get(ATTR_UNIT_OF_MEASUREMENT),
        device_class=state.attributes.get(ATTR_DEVICE_CLASS),
        state_class=state.attributes.get("state_class"),
    )


def _normalize_entity_ids(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        values: Iterable[Any] = (value,)
    elif isinstance(value, (list, tuple, set)):
        values = value
    else:
        return ()
    return tuple(item for item in values if isinstance(item, str))


def snapshot_event(hass: HomeAssistant, event: Event[Any]) -> EventSnapshot | None:
    """Copy only allowlisted Home Assistant data into a plain snapshot."""
    common = {
        "timestamp_ns": int(event.time_fired.timestamp() * 1_000_000_000),
        "context": _context_snapshot(event),
    }
    if event.event_type == EVENT_STATE_CHANGED:
        entity_id = event.data.get("entity_id")
        if not isinstance(entity_id, str):
            return None
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        return StateChanged(
            **common,
            entity=_entity_snapshot(hass, entity_id),
            old_state=_state_snapshot(old_state if isinstance(old_state, State) else None),
            new_state=_state_snapshot(new_state if isinstance(new_state, State) else None),
        )
    if event.event_type == EVENT_CALL_SERVICE:
        domain = event.data.get("domain")
        service = event.data.get("service")
        if not isinstance(domain, str) or not isinstance(service, str):
            return None
        service_data = event.data.get("service_data")
        entity_ids = (
            _normalize_entity_ids(service_data.get("entity_id"))
            if isinstance(service_data, dict)
            else ()
        )
        return ServiceCalled(**common, domain=domain, service=service, target_entity_ids=entity_ids)
    if event.event_type == EVENT_AUTOMATION_TRIGGERED:
        entity_id = event.data.get("entity_id")
        if not isinstance(entity_id, str):
            return None
        name = event.data.get("name")
        return AutomationTriggered(
            **common,
            entity_id=entity_id,
            name=name if isinstance(name, str) else None,
        )
    if event.event_type == EVENT_SYSTEM_LOG:
        logger_name = event.data.get("name")
        if not isinstance(logger_name, str):
            return None
        message_data = event.data.get("message", "")
        message = (
            " ".join(str(part) for part in message_data)
            if isinstance(message_data, (list, tuple))
            else str(message_data)
        )
        return SystemLogEvent(
            **common,
            level=str(event.data.get("level", "WARNING")),
            logger_name=logger_name,
            message=message,
            source=str(event.data.get("source", "")),
        )
    return LifecycleEvent(**common, event_type=event.event_type)


def build_record(
    hass: HomeAssistant,
    event: Event[Any],
    settings: EventSettings,
) -> TelemetryRecord | None:
    """Adapt and serialize one subscribed Home Assistant event."""
    snapshot = snapshot_event(hass, event)
    return serialize_event(snapshot, settings) if snapshot is not None else None
