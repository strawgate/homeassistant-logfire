"""Sanitize, validate, and serialize replayable Home Assistant event fixtures."""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

FIXTURE_SCHEMA_VERSION: Final = 1
NORMALIZED_TIMESTAMP: Final = "2026-01-01T00:00:00+00:00"
SUPPORTED_EVENT_TYPES: Final = (
    "automation_triggered",
    "call_service",
    "state_changed",
    "system_log_event",
)
FIXTURE_SOURCES: Final = (
    "sanitized-live-state-snapshot",
    "sanitized-live-websocket-capture",
)

_ALLOWED_STATE_ATTRIBUTES: Final = (
    "device_class",
    "state_class",
    "unit_of_measurement",
)
_SAFE_STATE_VALUES: Final = frozenset(
    {
        "closed",
        "closing",
        "home",
        "idle",
        "locked",
        "not_home",
        "off",
        "on",
        "open",
        "opening",
        "paused",
        "playing",
        "standby",
        "unavailable",
        "unknown",
        "unlocked",
    }
)
_SAFE_SERVICE_NAMES: Final = frozenset(
    {
        "close_cover",
        "lock",
        "open_cover",
        "pause",
        "play",
        "reload",
        "start",
        "stop",
        "toggle",
        "turn_off",
        "turn_on",
        "unlock",
        "update_entity",
    }
)
_ENTITY_ID_PATTERN: Final = re.compile(r"^(?P<domain>[a-z0-9_]+)\.[A-Za-z0-9_]+$")
_EVENT_TYPE_PATTERN: Final = re.compile(r"^[a-z0-9_]+$")
_SAFE_METADATA_PATTERN: Final = re.compile(r"^[A-Za-z0-9_%°/ .+*^·-]{1,64}$")
_NUMBER_PATTERN: Final = re.compile(r"^-?(?:\d+(?:\.\d*)?|\.\d+)$")
_VERSION_PATTERN: Final = re.compile(r"^\d{4}\.\d{1,2}(?:\.\d+)?(?:[A-Za-z0-9_.+-]*)?$")
_IPV4_PATTERN: Final = re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)")
_MAC_PATTERN: Final = re.compile(r"(?i)\b(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b")
_EMAIL_PATTERN: Final = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
_URL_PATTERN: Final = re.compile(r"(?i)\b(?:https?|wss?)://")
_JWT_PATTERN: Final = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_LOGFIRE_TOKEN_PATTERN: Final = re.compile(r"\bpylf_v\d+_[a-z]+_[A-Za-z0-9_-]+\b")
_BEARER_PATTERN: Final = re.compile(r"(?i)\bbearer\s+\S+")
_SECRET_KEY_PATTERN: Final = re.compile(
    r"(?i)(?:authorization|credential|password|secret|token|api[_-]?key)"
)


class UnsafeFixtureError(ValueError):
    """Raised when capture input or sanitized output violates the fixture contract."""


@dataclass(slots=True)
class _Pseudonymizer:
    """Create stable generic identifiers within one captured event."""

    identifiers: dict[tuple[str, str], str] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)

    def identifier(self, kind: str, value: str | None) -> str | None:
        if not value:
            return None
        key = (kind, value)
        if key not in self.identifiers:
            self.counts[kind] = self.counts.get(kind, 0) + 1
            self.identifiers[key] = f"{kind}-{self.counts[kind]:03d}"
        return self.identifiers[key]

    def entity_id(self, value: object) -> str | None:
        if not isinstance(value, str) or not (match := _ENTITY_ID_PATTERN.fullmatch(value)):
            return None
        domain = match.group("domain")
        key = ("entity", value)
        if key not in self.identifiers:
            self.counts[domain] = self.counts.get(domain, 0) + 1
            self.identifiers[key] = f"{domain}.sample_{self.counts[domain]:03d}"
        return self.identifiers[key]


def _require_mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise UnsafeFixtureError(f"{name} must be an object")
    return value


def _sanitize_context(value: object, pseudonyms: _Pseudonymizer) -> dict[str, str | None]:
    context = _require_mapping(value or {}, "event context")
    return {
        "id": pseudonyms.identifier("context", _optional_string(context.get("id"))),
        "parent_id": pseudonyms.identifier(
            "context",
            _optional_string(context.get("parent_id")),
        ),
        "user_id": pseudonyms.identifier("user", _optional_string(context.get("user_id"))),
    }


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _sanitize_state_value(value: object) -> str:
    state = str(value)
    if state in _SAFE_STATE_VALUES or _NUMBER_PATTERN.fullmatch(state):
        return state
    if "T" in state and (state.endswith("Z") or "+" in state):
        return NORMALIZED_TIMESTAMP
    return "sample"


def _sanitize_metadata(value: object) -> str | None:
    if value is None:
        return None
    candidate = str(value)[:64]
    return candidate if _SAFE_METADATA_PATTERN.fullmatch(candidate) else "sample"


def _sanitize_state(value: object, pseudonyms: _Pseudonymizer) -> dict[str, Any] | None:
    if value is None:
        return None
    state = _require_mapping(value, "state")
    entity_id = pseudonyms.entity_id(state.get("entity_id"))
    if entity_id is None:
        raise UnsafeFixtureError("state entity_id is invalid")
    raw_attributes = _require_mapping(state.get("attributes", {}), "state attributes")
    attributes = {
        key: sanitized
        for key in _ALLOWED_STATE_ATTRIBUTES
        if (sanitized := _sanitize_metadata(raw_attributes.get(key))) is not None
    }
    sanitized_state: dict[str, Any] = {
        "entity_id": entity_id,
        "state": _sanitize_state_value(state.get("state", "unknown")),
        "attributes": attributes,
        "last_changed": NORMALIZED_TIMESTAMP,
        "last_updated": NORMALIZED_TIMESTAMP,
        "context": _sanitize_context(state.get("context", {}), pseudonyms),
    }
    if "last_reported" in state:
        sanitized_state["last_reported"] = NORMALIZED_TIMESTAMP
    return sanitized_state


def _sanitize_entity_ids(value: object, pseudonyms: _Pseudonymizer) -> str | list[str] | None:
    if isinstance(value, str):
        return pseudonyms.entity_id(value)
    if isinstance(value, list):
        return [
            entity_id
            for item in value[:50]
            if (entity_id := pseudonyms.entity_id(item)) is not None
        ]
    return None


def _sanitize_state_changed(data: Mapping[str, Any], pseudonyms: _Pseudonymizer) -> dict[str, Any]:
    entity_id = pseudonyms.entity_id(data.get("entity_id"))
    if entity_id is None:
        raise UnsafeFixtureError("state_changed entity_id is invalid")
    return {
        "entity_id": entity_id,
        "old_state": _sanitize_state(data.get("old_state"), pseudonyms),
        "new_state": _sanitize_state(data.get("new_state"), pseudonyms),
    }


def _sanitize_service_call(data: Mapping[str, Any], pseudonyms: _Pseudonymizer) -> dict[str, Any]:
    domain = data.get("domain")
    service = data.get("service")
    if not isinstance(domain, str) or not _EVENT_TYPE_PATTERN.fullmatch(domain):
        raise UnsafeFixtureError("call_service domain is invalid")
    if not isinstance(service, str):
        raise UnsafeFixtureError("call_service service is invalid")
    service_data = _require_mapping(data.get("service_data", {}), "service_data")
    sanitized_service_data: dict[str, Any] = {}
    if (entity_ids := _sanitize_entity_ids(service_data.get("entity_id"), pseudonyms)) is not None:
        sanitized_service_data["entity_id"] = entity_ids
    return {
        "domain": domain,
        "service": service if service in _SAFE_SERVICE_NAMES else "sample_service",
        "service_data": sanitized_service_data,
    }


def _sanitize_automation(data: Mapping[str, Any], pseudonyms: _Pseudonymizer) -> dict[str, Any]:
    entity_id = pseudonyms.entity_id(data.get("entity_id"))
    if entity_id is None:
        raise UnsafeFixtureError("automation entity_id is invalid")
    return {"entity_id": entity_id, "name": "Sample automation"}


def _sanitize_system_log(data: Mapping[str, Any]) -> dict[str, Any]:
    source = data.get("source")
    return {
        "level": str(data.get("level", "WARNING")).upper()[:16],
        "message": ["Sanitized Home Assistant log message"],
        "name": "homeassistant.components.sample",
        "source": ["sample.py", 1] if isinstance(source, list) else "sample.py:1",
    }


def sanitize_event(
    event: Mapping[str, Any],
    *,
    homeassistant_version: str,
    source: str = "sanitized-live-websocket-capture",
) -> dict[str, Any]:
    """Return a deterministic fixture after minimizing live event data in memory."""
    if not _VERSION_PATTERN.fullmatch(homeassistant_version):
        raise UnsafeFixtureError("Home Assistant version is invalid")
    if source not in FIXTURE_SOURCES:
        raise UnsafeFixtureError("fixture source is invalid")
    event_type = event.get("event_type")
    if event_type not in SUPPORTED_EVENT_TYPES:
        raise UnsafeFixtureError(f"unsupported event type: {event_type!r}")
    data = _require_mapping(event.get("data", {}), "event data")
    pseudonyms = _Pseudonymizer()
    if event_type == "state_changed":
        sanitized_data = _sanitize_state_changed(data, pseudonyms)
    elif event_type == "call_service":
        sanitized_data = _sanitize_service_call(data, pseudonyms)
    elif event_type == "automation_triggered":
        sanitized_data = _sanitize_automation(data, pseudonyms)
    else:
        sanitized_data = _sanitize_system_log(data)

    fixture = {
        "schema_version": FIXTURE_SCHEMA_VERSION,
        "homeassistant_version": homeassistant_version,
        "source": source,
        "event": {
            "event_type": event_type,
            "data": sanitized_data,
            "origin": (
                event.get("origin") if event.get("origin") in ("LOCAL", "REMOTE") else "LOCAL"
            ),
            "time_fired": NORMALIZED_TIMESTAMP,
            "context": _sanitize_context(event.get("context", {}), pseudonyms),
        },
    }
    assert_fixture_safe(fixture)
    return fixture


def _walk(value: object, path: str = "$") -> Iterable[tuple[str, str, object]]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            yield child_path, str(key), child
            yield from _walk(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, f"{path}[{index}]")


def _exact_mapping(
    value: object,
    *,
    name: str,
    required: set[str],
    optional: set[str] | None = None,
) -> Mapping[str, Any]:
    mapping = _require_mapping(value, name)
    keys = set(mapping)
    optional = optional or set()
    if missing := required - keys:
        raise UnsafeFixtureError(f"{name} is missing fields: {', '.join(sorted(missing))}")
    if unexpected := keys - required - optional:
        raise UnsafeFixtureError(f"{name} has unexpected fields: {', '.join(sorted(unexpected))}")
    return mapping


def _validate_context(value: object, name: str) -> None:
    context = _exact_mapping(
        value,
        name=name,
        required={"id", "parent_id", "user_id"},
    )
    if any(context[key] is not None and not isinstance(context[key], str) for key in context):
        raise UnsafeFixtureError(f"{name} identifiers must be strings or null")


def _validate_state(value: object, expected_entity_id: object, name: str) -> None:
    if value is None:
        return
    state = _exact_mapping(
        value,
        name=name,
        required={
            "attributes",
            "context",
            "entity_id",
            "last_changed",
            "last_updated",
            "state",
        },
        optional={"last_reported"},
    )
    if state["entity_id"] != expected_entity_id:
        raise UnsafeFixtureError(f"{name} entity ID does not match the event")
    if not isinstance(state["state"], str):
        raise UnsafeFixtureError(f"{name} state must be a string")
    for timestamp_key in ("last_changed", "last_updated", "last_reported"):
        if timestamp_key in state and state[timestamp_key] != NORMALIZED_TIMESTAMP:
            raise UnsafeFixtureError(f"{name} timestamp is not normalized")
    attributes = _require_mapping(state["attributes"], f"{name} attributes")
    if unexpected := set(attributes) - set(_ALLOWED_STATE_ATTRIBUTES):
        raise UnsafeFixtureError(
            f"{name} attributes contain unexpected fields: {', '.join(sorted(unexpected))}"
        )
    if any(not isinstance(value, str) for value in attributes.values()):
        raise UnsafeFixtureError(f"{name} attributes must be strings")
    _validate_context(state["context"], f"{name} context")


def _validate_event_schema(event: Mapping[str, Any]) -> None:
    event = _exact_mapping(
        event,
        name="fixture event",
        required={"context", "data", "event_type", "origin", "time_fired"},
    )
    event_type = event["event_type"]
    if event_type not in SUPPORTED_EVENT_TYPES:
        raise UnsafeFixtureError("fixture event type is unsupported")
    if event["origin"] not in ("LOCAL", "REMOTE"):
        raise UnsafeFixtureError("fixture event origin is unsupported")
    if event["time_fired"] != NORMALIZED_TIMESTAMP:
        raise UnsafeFixtureError("fixture event timestamp is not normalized")
    _validate_context(event["context"], "fixture event context")

    if event_type == "state_changed":
        data = _exact_mapping(
            event["data"],
            name="state_changed data",
            required={"entity_id", "new_state", "old_state"},
        )
        if data["new_state"] is None and data["old_state"] is None:
            raise UnsafeFixtureError("state_changed must contain an old or new state")
        _validate_state(data["old_state"], data["entity_id"], "old state")
        _validate_state(data["new_state"], data["entity_id"], "new state")
        return
    if event_type == "call_service":
        data = _exact_mapping(
            event["data"],
            name="call_service data",
            required={"domain", "service", "service_data"},
        )
        if not all(isinstance(data[key], str) for key in ("domain", "service")):
            raise UnsafeFixtureError("call_service domain and service must be strings")
        service_data = _require_mapping(data["service_data"], "service_data")
        if set(service_data) - {"entity_id"}:
            raise UnsafeFixtureError("service_data has unexpected fields")
        return
    if event_type == "automation_triggered":
        data = _exact_mapping(
            event["data"],
            name="automation_triggered data",
            required={"entity_id", "name"},
        )
        if not all(isinstance(data[key], str) for key in ("entity_id", "name")):
            raise UnsafeFixtureError("automation fields must be strings")
        return
    data = _exact_mapping(
        event["data"],
        name="system_log_event data",
        required={"level", "message", "name", "source"},
    )
    if not all(isinstance(data[key], str) for key in ("level", "name")):
        raise UnsafeFixtureError("system log level and name must be strings")
    if not isinstance(data["message"], list) or not all(
        isinstance(part, str) for part in data["message"]
    ):
        raise UnsafeFixtureError("system log message must be a list of strings")
    if not isinstance(data["source"], (str, list)):
        raise UnsafeFixtureError("system log source must be a string or list")


def assert_fixture_safe(fixture: Mapping[str, Any]) -> None:
    """Reject fixture content that could contain credentials or household identifiers."""
    fixture = _exact_mapping(
        fixture,
        name="fixture",
        required={"event", "homeassistant_version", "schema_version", "source"},
    )
    if fixture["schema_version"] != FIXTURE_SCHEMA_VERSION:
        raise UnsafeFixtureError("fixture schema version is unsupported")
    if not isinstance(fixture["homeassistant_version"], str) or not _VERSION_PATTERN.fullmatch(
        fixture["homeassistant_version"]
    ):
        raise UnsafeFixtureError("fixture Home Assistant version is invalid")
    if fixture["source"] not in FIXTURE_SOURCES:
        raise UnsafeFixtureError("fixture source is invalid")
    event = _require_mapping(fixture["event"], "fixture event")
    _validate_event_schema(event)

    for path, key, value in _walk(fixture):
        if _SECRET_KEY_PATTERN.search(key):
            raise UnsafeFixtureError(f"secret-like field at {path}")
        if key == "entity_id" and value is not None:
            values = value if isinstance(value, list) else [value]
            if any(
                not isinstance(item, str) or re.fullmatch(r"[a-z0-9_]+\.sample_\d{3}", item) is None
                for item in values
            ):
                raise UnsafeFixtureError(f"unsanitized entity ID at {path}")
        if (
            key in {"id", "parent_id"}
            and ".context." in path
            and value is not None
            and (not isinstance(value, str) or re.fullmatch(r"context-\d{3}", value) is None)
        ):
            raise UnsafeFixtureError(f"unsanitized context ID at {path}")
        if (
            key == "user_id"
            and value is not None
            and (not isinstance(value, str) or re.fullmatch(r"user-\d{3}", value) is None)
        ):
            raise UnsafeFixtureError(f"unsanitized user ID at {path}")
        if not isinstance(value, str):
            continue
        if len(value) > 512:
            raise UnsafeFixtureError(f"oversized string at {path}")
        if any(
            pattern.search(value)
            for pattern in (
                _BEARER_PATTERN,
                _EMAIL_PATTERN,
                _IPV4_PATTERN,
                _JWT_PATTERN,
                _LOGFIRE_TOKEN_PATTERN,
                _MAC_PATTERN,
                _URL_PATTERN,
            )
        ):
            raise UnsafeFixtureError(f"sensitive value at {path}")


def fixture_json(fixture: Mapping[str, Any]) -> str:
    """Serialize a validated fixture deterministically."""
    assert_fixture_safe(fixture)
    return f"{json.dumps(fixture, indent=2, sort_keys=True, ensure_ascii=False)}\n"


def load_fixture(path: Path) -> dict[str, Any]:
    """Load and validate one checked-in fixture."""
    try:
        fixture = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise UnsafeFixtureError(f"unable to load fixture {path.name}") from error
    if not isinstance(fixture, dict):
        raise UnsafeFixtureError(f"fixture {path.name} must contain an object")
    assert_fixture_safe(fixture)
    return fixture


def fixture_paths(root: Path) -> list[Path]:
    """Return fixture files in deterministic order."""
    return sorted(root.glob("*.json"))


def main() -> None:
    """Validate all sanitized event fixtures in one directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    paths = fixture_paths(args.directory)
    if not paths:
        raise SystemExit(f"no fixtures found in {args.directory}")
    for path in paths:
        load_fixture(path)
    print(f"validated {len(paths)} sanitized Home Assistant event fixtures")


if __name__ == "__main__":
    main()
