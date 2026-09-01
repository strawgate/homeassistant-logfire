"""Pure aggregation of low-cardinality Home Assistant health metrics."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .records import EventSettings

type AttributeKey = tuple[tuple[str, str], ...]
type MetricValues = dict[AttributeKey, float]


@dataclass(frozen=True, slots=True)
class EntityHealth:
    """Minimum entity state needed for health aggregation."""

    entity_id: str
    domain: str
    state: str


@dataclass(frozen=True, slots=True)
class ConfigEntryHealth:
    """Minimum config-entry state needed for health aggregation."""

    domain: str
    state: str


@dataclass(frozen=True, slots=True)
class HealthSnapshot:
    """One point-in-time, framework-independent health sample."""

    entities: tuple[EntityHealth, ...]
    config_entries: tuple[ConfigEntryHealth, ...]
    setup_durations: tuple[tuple[str, float], ...]


@dataclass(frozen=True, slots=True)
class MetricBatch:
    """Gauge values grouped by stable instrument key."""

    entity_count: MetricValues
    unavailable_count: MetricValues
    config_entry_count: MetricValues
    setup_duration: MetricValues


def aggregate_health_metrics(
    snapshot: HealthSnapshot,
    filters: EventSettings,
) -> MetricBatch:
    """Aggregate a health snapshot without touching Home Assistant registries."""
    entity_counts: Counter[str] = Counter()
    unavailable_counts: Counter[str] = Counter()
    for entity in snapshot.entities:
        if not filters.accepts_entity(entity.entity_id):
            continue
        entity_counts[entity.domain] += 1
        if entity.state in ("unavailable", "unknown"):
            unavailable_counts[entity.domain] += 1

    config_entry_counts: Counter[tuple[str, str]] = Counter(
        (entry.domain, entry.state) for entry in snapshot.config_entries
    )
    return MetricBatch(
        entity_count={
            (("homeassistant.domain", domain),): float(count)
            for domain, count in entity_counts.items()
        },
        unavailable_count={
            (("homeassistant.domain", domain),): float(count)
            for domain, count in unavailable_counts.items()
        },
        config_entry_count={
            (
                ("homeassistant.domain", domain),
                ("homeassistant.config_entry.state", state),
            ): float(count)
            for (domain, state), count in config_entry_counts.items()
        },
        setup_duration={
            (("homeassistant.domain", domain),): float(duration)
            for domain, duration in snapshot.setup_durations
        },
    )
