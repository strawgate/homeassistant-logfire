"""Tests for deterministic in-memory sanitization of live Home Assistant events."""

from __future__ import annotations

from copy import deepcopy

import pytest

from tools.event_fixtures import (
    NORMALIZED_TIMESTAMP,
    UnsafeFixtureError,
    assert_fixture_safe,
    fixture_json,
    sanitize_event,
)


def _raw_state_event() -> dict:
    return {
        "event_type": "state_changed",
        "data": {
            "entity_id": "sensor.private_kitchen_temperature",
            "old_state": {
                "entity_id": "sensor.private_kitchen_temperature",
                "state": "20.1",
                "attributes": {
                    "device_class": "temperature",
                    "friendly_name": "Bill's private kitchen",
                    "secret": "pylf_v1_us_DoNotCommitThis",
                    "state_class": "measurement",
                    "unit_of_measurement": "°C",
                },
                "last_changed": "2026-09-01T12:34:56+00:00",
                "last_updated": "2026-09-01T12:34:56+00:00",
                "context": {
                    "id": "state-context-private",
                    "parent_id": None,
                    "user_id": "private-user-id",
                },
            },
            "new_state": {
                "entity_id": "sensor.private_kitchen_temperature",
                "state": "21.5",
                "attributes": {
                    "device_class": "temperature",
                    "friendly_name": "Bill's private kitchen",
                    "host": "192.168.1.223",
                    "state_class": "measurement",
                    "unit_of_measurement": "°C",
                },
                "last_changed": "2026-09-01T12:35:56+00:00",
                "last_updated": "2026-09-01T12:35:56+00:00",
                "last_reported": "2026-09-01T12:35:56+00:00",
                "context": {
                    "id": "event-context-private",
                    "parent_id": "state-context-private",
                    "user_id": "private-user-id",
                },
            },
        },
        "origin": "LOCAL",
        "time_fired": "2026-09-01T12:35:56+00:00",
        "context": {
            "id": "event-context-private",
            "parent_id": "state-context-private",
            "user_id": "private-user-id",
        },
    }


def test_state_event_is_sanitized_before_serialization() -> None:
    fixture = sanitize_event(_raw_state_event(), homeassistant_version="2026.8.3")

    event = fixture["event"]
    assert event["time_fired"] == NORMALIZED_TIMESTAMP
    assert event["data"]["entity_id"] == "sensor.sample_001"
    assert event["data"]["new_state"]["entity_id"] == "sensor.sample_001"
    assert event["data"]["new_state"]["attributes"] == {
        "device_class": "temperature",
        "state_class": "measurement",
        "unit_of_measurement": "°C",
    }
    assert event["data"]["new_state"]["last_reported"] == NORMALIZED_TIMESTAMP
    assert event["context"] == {
        "id": "context-002",
        "parent_id": "context-001",
        "user_id": "user-001",
    }
    serialized = fixture_json(fixture)
    for private_value in (
        "Bill",
        "192.168.1.223",
        "DoNotCommitThis",
        "private_kitchen",
        "private-user-id",
    ):
        assert private_value not in serialized


def test_sanitization_is_deterministic() -> None:
    first = sanitize_event(_raw_state_event(), homeassistant_version="2026.8.3")
    second = sanitize_event(_raw_state_event(), homeassistant_version="2026.8.3")

    assert fixture_json(first) == fixture_json(second)


def test_service_call_keeps_targets_but_drops_arbitrary_service_data() -> None:
    fixture = sanitize_event(
        {
            "event_type": "call_service",
            "data": {
                "domain": "light",
                "service": "turn_on",
                "service_data": {
                    "brightness": 255,
                    "entity_id": ["light.private_kitchen", "light.private_bedroom"],
                    "password": "do-not-keep",
                },
            },
            "context": {},
        },
        homeassistant_version="2026.8.3",
    )

    assert fixture["event"]["data"] == {
        "domain": "light",
        "service": "turn_on",
        "service_data": {"entity_id": ["light.sample_001", "light.sample_002"]},
    }


def test_safety_scan_rejects_a_sensitive_value_after_sanitization() -> None:
    fixture = sanitize_event(_raw_state_event(), homeassistant_version="2026.8.3")
    unsafe_fixture = deepcopy(fixture)
    unsafe_fixture["event"]["data"]["new_state"]["state"] = "Bearer leaked-token"

    with pytest.raises(UnsafeFixtureError, match="sensitive value"):
        assert_fixture_safe(unsafe_fixture)


def test_safety_scan_rejects_fixture_schema_drift() -> None:
    fixture = sanitize_event(_raw_state_event(), homeassistant_version="2026.8.3")
    fixture["event"]["data"]["new_state"]["attributes"]["friendly_name"] = "Sample room"

    with pytest.raises(UnsafeFixtureError, match="unexpected fields: friendly_name"):
        assert_fixture_safe(fixture)


def test_invalid_or_unsupported_event_is_never_persistable() -> None:
    with pytest.raises(UnsafeFixtureError, match="unsupported event type"):
        sanitize_event(
            {"event_type": "everything", "data": {}, "context": {}},
            homeassistant_version="2026.8.3",
        )
