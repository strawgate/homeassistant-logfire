"""Tests for bounded application delivery."""

from unittest.mock import MagicMock

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, State
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.logfire.events import EventSettings
from custom_components.logfire.pipeline import PipelineSettings, TelemetryPipeline


def test_full_queue_drops_new_record(hass: HomeAssistant) -> None:
    client = MagicMock()
    events_counter = MagicMock()
    dropped_counter = MagicMock()
    client.meter.create_counter.side_effect = [events_counter, dropped_counter]
    client.meter.create_gauge.return_value = MagicMock()
    settings = PipelineSettings(
        events=EventSettings(
            export_automations=True,
            export_service_calls=True,
            export_state_changes=True,
            export_system_logs=True,
            include_domains=frozenset(),
            exclude_entities=frozenset(),
        ),
        export_metrics=False,
        metric_interval=60,
        queue_size=1,
    )
    entry = MockConfigEntry(domain="logfire", data={}, options={})
    pipeline = TelemetryPipeline(hass, entry, client, settings)
    event = Event(
        EVENT_STATE_CHANGED,
        {
            "entity_id": "sensor.temperature",
            "old_state": State("sensor.temperature", "20"),
            "new_state": State("sensor.temperature", "21"),
        },
    )

    pipeline.handle_event(event)
    pipeline.handle_event(event)

    assert pipeline.stats.enqueued == 1
    assert pipeline.stats.dropped == 1
    assert pipeline.diagnostics()["queue_size"] == 1
    events_counter.add.assert_not_called()
    dropped_counter.add.assert_called_once_with(
        1,
        {"reason": "application_queue_full"},
    )
