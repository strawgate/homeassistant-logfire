"""Home Assistant-independent telemetry export core."""

from .delivery import DeliveryOutcome, DeliveryQueue, DeliveryStats
from .metrics import (
    ConfigEntryHealth,
    EntityHealth,
    HealthSnapshot,
    MetricBatch,
    aggregate_health_metrics,
)
from .model import (
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
from .otlp import LogfireOtelClient, endpoint_for_token
from .records import EventSettings, serialize_event

__all__ = [
    "AutomationTriggered",
    "ConfigEntryHealth",
    "ContextSnapshot",
    "DeliveryOutcome",
    "DeliveryQueue",
    "DeliveryStats",
    "EntityHealth",
    "EntitySnapshot",
    "EventSettings",
    "EventSnapshot",
    "HealthSnapshot",
    "LifecycleEvent",
    "LogfireOtelClient",
    "MetricBatch",
    "ServiceCalled",
    "StateChanged",
    "StateSnapshot",
    "SystemLogEvent",
    "TelemetryRecord",
    "aggregate_health_metrics",
    "endpoint_for_token",
    "serialize_event",
]
