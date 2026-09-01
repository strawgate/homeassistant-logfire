"""Tests for deterministic low-cardinality health aggregation."""

from custom_components.logfire.core.metrics import (
    ConfigEntryHealth,
    EntityHealth,
    HealthSnapshot,
    aggregate_health_metrics,
)
from custom_components.logfire.core.records import EventSettings


def test_health_metrics_are_aggregated_and_filtered() -> None:
    snapshot = HealthSnapshot(
        entities=(
            EntityHealth("sensor.temperature", "sensor", "21.5"),
            EntityHealth("sensor.offline", "sensor", "unavailable"),
            EntityHealth("light.kitchen", "light", "unknown"),
            EntityHealth("light.excluded", "light", "on"),
            EntityHealth("camera.front", "camera", "streaming"),
        ),
        config_entries=(
            ConfigEntryHealth("mqtt", "loaded"),
            ConfigEntryHealth("mqtt", "setup_retry"),
            ConfigEntryHealth("zha", "loaded"),
        ),
        setup_durations=(("mqtt", 1.25), ("zha", 2.5)),
    )
    filters = EventSettings(
        export_automations=True,
        export_service_calls=True,
        export_state_changes=True,
        export_system_logs=True,
        include_domains=frozenset({"sensor", "light"}),
        exclude_entities=frozenset({"light.excluded"}),
    )

    batch = aggregate_health_metrics(snapshot, filters)

    assert batch.entity_count == {
        (("homeassistant.domain", "sensor"),): 2.0,
        (("homeassistant.domain", "light"),): 1.0,
    }
    assert batch.unavailable_count == {
        (("homeassistant.domain", "sensor"),): 1.0,
        (("homeassistant.domain", "light"),): 1.0,
    }
    assert batch.config_entry_count == {
        (
            ("homeassistant.domain", "mqtt"),
            ("homeassistant.config_entry.state", "loaded"),
        ): 1.0,
        (
            ("homeassistant.domain", "mqtt"),
            ("homeassistant.config_entry.state", "setup_retry"),
        ): 1.0,
        (
            ("homeassistant.domain", "zha"),
            ("homeassistant.config_entry.state", "loaded"),
        ): 1.0,
    }
    assert batch.setup_duration == {
        (("homeassistant.domain", "mqtt"),): 1.25,
        (("homeassistant.domain", "zha"),): 2.5,
    }
