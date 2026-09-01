"""Plain snapshots and emitted records shared by adapters and tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from opentelemetry._logs import SeverityNumber


@dataclass(frozen=True, slots=True)
class ContextSnapshot:
    """Non-user correlation identifiers for one Home Assistant event."""

    context_id: str | None = None
    parent_id: str | None = None


@dataclass(frozen=True, slots=True)
class EntitySnapshot:
    """Allowlisted entity-registry metadata."""

    entity_id: str
    device_id: str | None = None
    area_id: str | None = None
    label_ids: tuple[str, ...] = ()

    @property
    def domain(self) -> str:
        """Return the entity domain."""
        return self.entity_id.partition(".")[0]


@dataclass(frozen=True, slots=True)
class StateSnapshot:
    """Allowlisted state data; arbitrary state attributes never enter the core."""

    value: str
    unit: str | None = None
    device_class: str | None = None
    state_class: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class BaseEvent:
    """Fields present on every event snapshot."""

    timestamp_ns: int
    context: ContextSnapshot = field(default_factory=ContextSnapshot)


@dataclass(frozen=True, slots=True, kw_only=True)
class StateChanged(BaseEvent):
    """A state transition and its allowlisted registry metadata."""

    entity: EntitySnapshot
    old_state: StateSnapshot | None = None
    new_state: StateSnapshot | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ServiceCalled(BaseEvent):
    """A service call with entity targets only, not arbitrary service data."""

    domain: str
    service: str
    target_entity_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class AutomationTriggered(BaseEvent):
    """An automation trigger event."""

    entity_id: str
    name: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SystemLogEvent(BaseEvent):
    """A bounded system-log candidate awaiting core redaction."""

    level: str
    logger_name: str
    message: str
    source: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class LifecycleEvent(BaseEvent):
    """A Home Assistant lifecycle event."""

    event_type: str


type EventSnapshot = (
    StateChanged | ServiceCalled | AutomationTriggered | SystemLogEvent | LifecycleEvent
)


@dataclass(frozen=True, slots=True)
class TelemetryRecord:
    """A minimized event ready for OpenTelemetry emission."""

    event_name: str
    body: str
    attributes: dict[str, Any]
    timestamp_ns: int
    severity_number: SeverityNumber = SeverityNumber.INFO
    severity_text: str = "INFO"
