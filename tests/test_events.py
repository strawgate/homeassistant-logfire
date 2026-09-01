"""Tests for privacy-preserving event serialization."""

from homeassistant.const import EVENT_CALL_SERVICE, EVENT_STATE_CHANGED
from homeassistant.core import Context, Event, HomeAssistant, State

from custom_components.logfire.const import EVENT_SYSTEM_LOG
from custom_components.logfire.events import EventSettings, build_record


def _settings(**changes) -> EventSettings:
    values = {
        "export_automations": True,
        "export_service_calls": True,
        "export_state_changes": True,
        "export_system_logs": True,
        "include_domains": frozenset(),
        "exclude_entities": frozenset(),
    }
    values.update(changes)
    return EventSettings(**values)


def test_state_change_has_stable_fields_without_user_or_arbitrary_attributes(
    hass: HomeAssistant,
) -> None:
    event = Event(
        EVENT_STATE_CHANGED,
        {
            "entity_id": "sensor.kitchen_temperature",
            "old_state": State("sensor.kitchen_temperature", "20.0"),
            "new_state": State(
                "sensor.kitchen_temperature",
                "21.5",
                {
                    "device_class": "temperature",
                    "friendly_name": "Private room name",
                    "secret": "must-not-export",
                    "state_class": "measurement",
                    "unit_of_measurement": "°C",
                },
            ),
        },
        context=Context(id="context-id", user_id="private-user-id"),
    )

    record = build_record(hass, event, _settings())

    assert record is not None
    assert record.event_name == "homeassistant.state_changed"
    assert record.attributes["homeassistant.state.new"] == "21.5"
    assert record.attributes["homeassistant.entity.unit"] == "°C"
    assert record.attributes["homeassistant.context.id"] == "context-id"
    assert "homeassistant.context.user_id" not in record.attributes
    assert "secret" not in record.attributes
    assert "friendly_name" not in record.attributes


def test_service_targets_respect_entity_filters(hass: HomeAssistant) -> None:
    event = Event(
        EVENT_CALL_SERVICE,
        {
            "domain": "light",
            "service": "turn_on",
            "service_data": {"entity_id": ["light.kitchen", "light.bedroom"]},
        },
    )

    record = build_record(
        hass,
        event,
        _settings(exclude_entities=frozenset({"light.bedroom"})),
    )

    assert record is not None
    assert record.attributes["homeassistant.target.entity.ids"] == ("light.kitchen",)
    assert "service_data" not in record.attributes


def test_exporter_system_logs_are_dropped(hass: HomeAssistant) -> None:
    event = Event(
        EVENT_SYSTEM_LOG,
        {
            "level": "ERROR",
            "message": ["export failed"],
            "name": "opentelemetry.exporter.otlp.proto.http",
            "source": ["exporter.py", 42],
        },
    )

    assert build_record(hass, event, _settings()) is None


def test_system_log_messages_redact_common_secret_shapes(hass: HomeAssistant) -> None:
    event = Event(
        EVENT_SYSTEM_LOG,
        {
            "level": "ERROR",
            "message": [
                "request failed",
                "Authorization: Bearer-secret",
                "pylf_v1_us_SecretToken123",
            ],
            "name": "homeassistant.components.example",
            "source": ["example.py", 42],
        },
    )

    record = build_record(hass, event, _settings())

    assert record is not None
    assert "Bearer-secret" not in record.body
    assert "SecretToken123" not in record.body
    assert record.body.count("<redacted>") == 2
