"""Contract tests for pure privacy-preserving record serialization."""

from __future__ import annotations

from opentelemetry._logs import SeverityNumber

from custom_components.logfire.core.model import (
    AutomationTriggered,
    ContextSnapshot,
    EntitySnapshot,
    LifecycleEvent,
    ServiceCalled,
    StateChanged,
    StateSnapshot,
    SystemLogEvent,
)
from custom_components.logfire.core.records import EventSettings, serialize_event


def _settings(**changes: object) -> EventSettings:
    values = {
        "export_automations": True,
        "export_service_calls": True,
        "export_state_changes": True,
        "export_system_logs": True,
        "include_domains": frozenset(),
        "exclude_entities": frozenset(),
    }
    values.update(changes)
    return EventSettings(**values)  # type: ignore[arg-type]


def test_state_change_exports_only_allowlisted_snapshot_fields() -> None:
    event = StateChanged(
        timestamp_ns=1_700_000_000_123_456_789,
        context=ContextSnapshot(context_id="context-id", parent_id="parent-id"),
        entity=EntitySnapshot(
            "sensor.kitchen_temperature",
            device_id="device-id",
            area_id="kitchen",
            label_ids=("climate",),
        ),
        old_state=StateSnapshot("20.0"),
        new_state=StateSnapshot(
            "21.5",
            unit="°C",
            device_class="temperature",
            state_class="measurement",
        ),
    )

    record = serialize_event(event, _settings())

    assert record is not None
    assert record.event_name == "homeassistant.state_changed"
    assert record.timestamp_ns == 1_700_000_000_123_456_789
    assert record.attributes == {
        "event.name": "homeassistant.state_changed",
        "homeassistant.entity.domain": "sensor",
        "homeassistant.entity.id": "sensor.kitchen_temperature",
        "homeassistant.device.id": "device-id",
        "homeassistant.area.id": "kitchen",
        "homeassistant.label.ids": ("climate",),
        "homeassistant.state.old": "20.0",
        "homeassistant.state.new": "21.5",
        "homeassistant.entity.unit": "°C",
        "homeassistant.entity.device_class": "temperature",
        "homeassistant.entity.state_class": "measurement",
        "homeassistant.context.id": "context-id",
        "homeassistant.context.parent_id": "parent-id",
    }


def test_entity_filters_drop_state_and_all_filtered_service_targets() -> None:
    settings = _settings(
        include_domains=frozenset({"light"}),
        exclude_entities=frozenset({"light.bedroom"}),
    )
    state = StateChanged(
        timestamp_ns=1,
        entity=EntitySnapshot("sensor.temperature"),
        new_state=StateSnapshot("21"),
    )
    service = ServiceCalled(
        timestamp_ns=2,
        domain="light",
        service="turn_on",
        target_entity_ids=("light.bedroom", "sensor.temperature"),
    )

    assert serialize_event(state, settings) is None
    assert serialize_event(service, settings) is None


def test_service_targets_are_filtered_and_bounded() -> None:
    event = ServiceCalled(
        timestamp_ns=1,
        domain="light",
        service="turn_on",
        target_entity_ids=tuple(f"light.target_{index}" for index in range(60)),
    )

    record = serialize_event(event, _settings(exclude_entities={"light.target_4"}))

    assert record is not None
    targets = record.attributes["homeassistant.target.entity.ids"]
    assert len(targets) == 49
    assert targets[0] == "light.target_0"
    assert targets[-1] == "light.target_49"
    assert "light.target_4" not in targets


def test_system_log_redacts_credentials_and_maps_severity() -> None:
    event = SystemLogEvent(
        timestamp_ns=1,
        level="error",
        logger_name="homeassistant.components.example",
        message=(
            "Authorization: Bearer-secret token=plain-secret api_key:abc pylf_v1_us_LogfireSecret"
        ),
        source="example.py:42",
    )

    record = serialize_event(event, _settings())

    assert record is not None
    assert record.body == "Authorization: <redacted> token=<redacted> api_key:<redacted> <redacted>"
    assert record.severity_number is SeverityNumber.ERROR
    assert record.severity_text == "ERROR"


def test_exporter_system_logs_do_not_feed_back_into_exporter() -> None:
    event = SystemLogEvent(
        timestamp_ns=1,
        level="ERROR",
        logger_name="opentelemetry.exporter.otlp.proto.http",
        message="export failed",
    )

    assert serialize_event(event, _settings()) is None


def test_automation_respects_event_toggle_and_entity_filter() -> None:
    event = AutomationTriggered(
        timestamp_ns=1,
        entity_id="automation.arrive_home",
        name="Arrive home",
    )

    record = serialize_event(event, _settings())

    assert record is not None
    assert record.attributes["homeassistant.automation.id"] == "automation.arrive_home"
    assert record.attributes["homeassistant.automation.name"] == "Arrive home"
    assert serialize_event(event, _settings(export_automations=False)) is None
    assert (
        serialize_event(
            event,
            _settings(exclude_entities=frozenset({"automation.arrive_home"})),
        )
        is None
    )


def test_lifecycle_event_has_stable_event_name_and_context() -> None:
    event = LifecycleEvent(
        timestamp_ns=123,
        context=ContextSnapshot(context_id="context-id"),
        event_type="homeassistant_started",
    )

    record = serialize_event(event, _settings())

    assert record is not None
    assert record.event_name == "homeassistant.homeassistant_started"
    assert record.body == "Home Assistant lifecycle: homeassistant_started"
    assert record.attributes == {
        "event.name": "homeassistant.homeassistant_started",
        "homeassistant.context.id": "context-id",
    }
