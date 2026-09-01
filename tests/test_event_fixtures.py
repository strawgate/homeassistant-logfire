"""Replay sanitized live Home Assistant payloads through the integration adapter."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from homeassistant.core import Context, Event, EventOrigin, HomeAssistant, State

from custom_components.logfire.core.model import StateChanged
from custom_components.logfire.core.records import EventSettings, serialize_event
from custom_components.logfire.events import snapshot_event
from tools.event_fixtures import fixture_paths, load_fixture

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "homeassistant"
FIXTURES = fixture_paths(FIXTURE_ROOT)


def _settings() -> EventSettings:
    return EventSettings(
        export_automations=True,
        export_service_calls=True,
        export_state_changes=True,
        export_system_logs=True,
        include_domains=frozenset(),
        exclude_entities=frozenset(),
    )


def _state_from_fixture(value: dict[str, Any] | None) -> State | None:
    if value is None:
        return None
    state = State.from_dict(deepcopy(value))
    assert state is not None
    return state


def _event_from_fixture(fixture: dict[str, Any]) -> Event[dict[str, Any]]:
    fixture_event = fixture["event"]
    data = deepcopy(fixture_event["data"])
    if fixture_event["event_type"] == "state_changed":
        data["old_state"] = _state_from_fixture(data["old_state"])
        data["new_state"] = _state_from_fixture(data["new_state"])
    context = fixture_event["context"]
    return Event(
        fixture_event["event_type"],
        data,
        origin=EventOrigin(fixture_event["origin"]),
        time_fired_timestamp=datetime.fromisoformat(fixture_event["time_fired"]).timestamp(),
        context=Context(
            id=context["id"],
            parent_id=context["parent_id"],
            user_id=context["user_id"],
        ),
    )


def test_fixture_corpus_covers_live_events_and_state_snapshots() -> None:
    fixtures = [load_fixture(path) for path in FIXTURES]

    assert len(fixtures) >= 10
    assert {fixture["source"] for fixture in fixtures} == {
        "sanitized-live-state-snapshot",
        "sanitized-live-websocket-capture",
    }
    assert {
        fixture["event"]["data"]["new_state"]["attributes"].get("device_class")
        for fixture in fixtures
    } >= {"battery", "current", "energy", "humidity", "power", "temperature"}


@pytest.mark.parametrize("fixture_path", FIXTURES, ids=lambda path: path.stem)
def test_sanitized_live_event_replays_through_adapter(
    hass: HomeAssistant,
    fixture_path: Path,
) -> None:
    fixture = load_fixture(fixture_path)
    fixture_event = fixture["event"]

    snapshot = snapshot_event(hass, _event_from_fixture(fixture))

    assert isinstance(snapshot, StateChanged)
    assert snapshot.entity.entity_id == fixture_event["data"]["entity_id"]
    assert snapshot.timestamp_ns == int(
        datetime.fromisoformat(fixture_event["time_fired"]).timestamp() * 1_000_000_000
    )
    for field_name in ("old_state", "new_state"):
        expected = fixture_event["data"][field_name]
        actual = getattr(snapshot, field_name)
        if expected is None:
            assert actual is None
            continue
        assert actual is not None
        assert actual.value == expected["state"]
        assert actual.unit == expected["attributes"].get("unit_of_measurement")
        assert actual.device_class == expected["attributes"].get("device_class")
        assert actual.state_class == expected["attributes"].get("state_class")

    record = serialize_event(snapshot, _settings())

    assert record is not None
    assert record.event_name == "homeassistant.state_changed"
    assert record.attributes["event.name"] == "homeassistant.state_changed"
    assert record.attributes["homeassistant.entity.id"] == fixture_event["data"]["entity_id"]
    assert (
        record.attributes["homeassistant.state.new"] == fixture_event["data"]["new_state"]["state"]
    )
    assert "homeassistant.context.user_id" not in record.attributes
