"""Capture selected Home Assistant WebSocket events as sanitized replay fixtures."""

from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import sys
import time
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Final
from urllib.parse import urlsplit, urlunsplit

from websockets.sync.client import ClientConnection, connect

from .event_fixtures import (
    SUPPORTED_EVENT_TYPES,
    fixture_json,
    fixture_paths,
    load_fixture,
    sanitize_event,
)

_DEFAULT_URL: Final = "http://homeassistant.local:8123"


def _websocket_url(base_url: str) -> str:
    parsed = urlsplit(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("HOMEASSISTANT_URL must be an http or https URL")
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunsplit((scheme, parsed.netloc, "/api/websocket", "", ""))


def _receive_json(connection: ClientConnection, timeout: float) -> dict[str, Any]:
    message = connection.recv(timeout=timeout)
    if isinstance(message, bytes):
        message = message.decode()
    value = json.loads(message)
    if not isinstance(value, dict):
        raise RuntimeError("Home Assistant returned a non-object WebSocket message")
    return value


def _event_fingerprint(event: Mapping[str, Any]) -> tuple[object, ...]:
    event_type = event.get("event_type")
    data = event.get("data") if isinstance(event.get("data"), Mapping) else {}
    if event_type == "state_changed":
        state = data.get("new_state") or data.get("old_state")
        state = state if isinstance(state, Mapping) else {}
        attributes = state.get("attributes")
        attributes = attributes if isinstance(attributes, Mapping) else {}
        entity_id = data.get("entity_id")
        domain = entity_id.partition(".")[0] if isinstance(entity_id, str) else "unknown"
        if data.get("new_state") is None:
            transition = "removed"
        elif data.get("old_state") is None:
            transition = "added"
        else:
            transition = "changed"
        return (
            event_type,
            domain,
            attributes.get("device_class"),
            attributes.get("state_class"),
            attributes.get("unit_of_measurement"),
            transition,
        )
    if event_type == "call_service":
        return event_type, data.get("domain"), data.get("service")
    return (event_type,)


def _fixture_shape(fixture: Mapping[str, Any]) -> tuple[object, ...]:
    event = fixture["event"]
    data = event["data"]
    event_type = event["event_type"]
    if event_type == "state_changed":
        state = data.get("new_state") or data.get("old_state") or {}
        attributes = state.get("attributes", {})
        transition = (
            "removed"
            if data.get("new_state") is None
            else "added"
            if data.get("old_state") is None
            else "changed"
        )
        return (
            event_type,
            data["entity_id"].partition(".")[0],
            attributes.get("device_class"),
            attributes.get("state_class"),
            attributes.get("unit_of_measurement"),
            transition,
        )
    if event_type == "call_service":
        return event_type, data.get("domain"), data.get("service")
    return (event_type,)


def _slug(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")[:40] or "sample"


def _fixture_name(fixture: Mapping[str, Any], index: int) -> str:
    event = fixture["event"]
    event_type = event["event_type"]
    qualifier = "sample"
    if event_type == "state_changed":
        state = event["data"].get("new_state") or event["data"].get("old_state") or {}
        qualifier = (
            state.get("attributes", {}).get("device_class")
            or event["data"]["entity_id"].partition(".")[0]
        )
    return f"{_slug(event_type)}_{_slug(qualifier)}_{index:02d}.json"


def _write_fixture(path: Path, fixture: Mapping[str, Any], *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite existing fixture {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as temporary:
        temporary.write(fixture_json(fixture))
        temporary_path = Path(temporary.name)
    temporary_path.replace(path)


def _authenticate(connection: ClientConnection, token: str) -> str:
    if _receive_json(connection, 10).get("type") != "auth_required":
        raise RuntimeError("Home Assistant did not request WebSocket authentication")
    connection.send(json.dumps({"type": "auth", "access_token": token}))
    response = _receive_json(connection, 10)
    if response.get("type") != "auth_ok":
        raise RuntimeError("Home Assistant rejected the access token")
    version = response.get("ha_version")
    if not isinstance(version, str):
        raise RuntimeError("Home Assistant omitted its version from auth_ok")
    return version


def _subscribe(
    connection: ClientConnection,
    event_types: tuple[str, ...],
) -> list[dict[str, Any]]:
    pending_events: list[dict[str, Any]] = []
    for command_id, event_type in enumerate(event_types, 1):
        connection.send(
            json.dumps(
                {
                    "id": command_id,
                    "type": "subscribe_events",
                    "event_type": event_type,
                }
            )
        )
        while True:
            response = _receive_json(connection, 10)
            if response.get("type") == "event":
                pending_events.append(response)
                continue
            if response.get("type") == "result" and response.get("id") == command_id:
                if response.get("success") is not True:
                    raise RuntimeError(f"Home Assistant refused {event_type} subscription")
                break
    return pending_events


def _get_states(connection: ClientConnection, command_id: int = 1000) -> list[Mapping[str, Any]]:
    connection.send(json.dumps({"id": command_id, "type": "get_states"}))
    while True:
        response = _receive_json(connection, 10)
        if response.get("type") != "result" or response.get("id") != command_id:
            continue
        if response.get("success") is not True or not isinstance(response.get("result"), list):
            raise RuntimeError("Home Assistant refused get_states")
        return [state for state in response["result"] if isinstance(state, Mapping)]


def _state_snapshot_events(
    states: list[Mapping[str, Any]],
    count: int,
) -> list[Mapping[str, Any]]:
    safe_domains = {
        "binary_sensor",
        "cover",
        "input_boolean",
        "light",
        "sensor",
        "switch",
        "update",
    }
    preferred_device_classes = {
        device_class: index
        for index, device_class in enumerate(
            (
                "temperature",
                "humidity",
                "battery",
                "energy",
                "power",
                "current",
                "voltage",
                "timestamp",
                "data_size",
                "duration",
                "frequency",
                "illuminance",
                "pressure",
                "signal_strength",
            )
        )
    }

    def state_sort_key(state: Mapping[str, Any]) -> tuple[object, ...]:
        attributes = state.get("attributes")
        attributes = attributes if isinstance(attributes, Mapping) else {}
        device_class = attributes.get("device_class")
        return (
            preferred_device_classes.get(device_class, len(preferred_device_classes)),
            str(device_class),
            str(attributes.get("state_class")),
            str(attributes.get("unit_of_measurement")),
            str(state.get("entity_id")),
        )

    selected: list[Mapping[str, Any]] = []
    fingerprints: set[tuple[object, ...]] = set()
    for state in sorted(states, key=state_sort_key):
        entity_id = state.get("entity_id")
        if not isinstance(entity_id, str) or entity_id.partition(".")[0] not in safe_domains:
            continue
        attributes = state.get("attributes")
        attributes = attributes if isinstance(attributes, Mapping) else {}
        fingerprint = (
            entity_id.partition(".")[0],
            attributes.get("device_class"),
            attributes.get("state_class"),
            attributes.get("unit_of_measurement"),
        )
        if fingerprint in fingerprints:
            continue
        fingerprints.add(fingerprint)
        selected.append(
            {
                "event_type": "state_changed",
                "data": {
                    "entity_id": entity_id,
                    "old_state": None,
                    "new_state": state,
                },
                "origin": "LOCAL",
                "time_fired": state.get("last_updated"),
                "context": state.get("context", {}),
            }
        )
        if len(selected) >= count:
            break
    return selected


def _capture(
    connection: ClientConnection,
    pending: list[dict[str, Any]],
    *,
    event_types: tuple[str, ...],
    duration: float,
    max_per_type: int,
) -> list[Mapping[str, Any]]:
    captured: list[Mapping[str, Any]] = []
    fingerprints: set[tuple[object, ...]] = set()
    counts: Counter[str] = Counter()
    deadline = time.monotonic() + duration
    messages = iter(pending)
    while time.monotonic() < deadline:
        try:
            message = next(messages)
        except StopIteration:
            try:
                message = _receive_json(connection, min(1.0, deadline - time.monotonic()))
            except TimeoutError:
                continue
        event = message.get("event")
        if message.get("type") != "event" or not isinstance(event, Mapping):
            continue
        event_type = event.get("event_type")
        if event_type not in event_types or counts[event_type] >= max_per_type:
            continue
        fingerprint = _event_fingerprint(event)
        if fingerprint in fingerprints:
            continue
        fingerprints.add(fingerprint)
        counts[event_type] += 1
        captured.append(event)
        if all(counts[event_type] >= max_per_type for event_type in event_types):
            break
    return captured


def _ssl_context(url: str, verify_tls: bool) -> ssl.SSLContext | None:
    if not url.startswith("wss://"):
        return None
    context = ssl.create_default_context()
    if not verify_tls:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


def main() -> None:
    """Capture a bounded, diverse set of live events without persisting raw messages."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("tests/fixtures/homeassistant"))
    parser.add_argument("--duration", type=float, default=30)
    parser.add_argument("--max-per-type", type=int, default=3)
    parser.add_argument("--state-snapshot-count", type=int, default=0)
    parser.add_argument("--no-live-events", action="store_true")
    parser.add_argument(
        "--event-type",
        action="append",
        choices=SUPPORTED_EVENT_TYPES,
        dest="event_types",
    )
    parser.add_argument("--url", default=os.getenv("HOMEASSISTANT_URL", _DEFAULT_URL))
    parser.add_argument(
        "--tls-verify",
        action=argparse.BooleanOptionalAction,
        default=os.getenv("HOMEASSISTANT_TLS_VERIFY", "true").lower() == "true",
    )
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if args.duration <= 0 or args.duration > 300:
        parser.error("--duration must be greater than 0 and no more than 300 seconds")
    if args.max_per_type < 1 or args.max_per_type > 20:
        parser.error("--max-per-type must be between 1 and 20")
    if args.state_snapshot_count < 0 or args.state_snapshot_count > 20:
        parser.error("--state-snapshot-count must be between 0 and 20")
    if args.no_live_events and args.state_snapshot_count == 0:
        parser.error("--no-live-events requires --state-snapshot-count")
    token = os.getenv("HOMEASSISTANT_TOKEN") or os.getenv("HOMEASSISTANT_AUTOMATION_TOKEN")
    if not token or len(token) < 20 or token.startswith("REPLACE"):
        parser.error("HOMEASSISTANT_TOKEN or HOMEASSISTANT_AUTOMATION_TOKEN is required")

    event_types = tuple(dict.fromkeys(args.event_types or SUPPORTED_EVENT_TYPES))
    websocket_url = _websocket_url(args.url)
    with connect(
        websocket_url,
        ssl=_ssl_context(websocket_url, args.tls_verify),
        open_timeout=10,
        max_size=4 * 1024 * 1024,
        proxy=None,
    ) as connection:
        homeassistant_version = _authenticate(connection, token)
        state_events = (
            _state_snapshot_events(_get_states(connection), args.state_snapshot_count)
            if args.state_snapshot_count
            else []
        )
        if args.no_live_events:
            captured = []
        else:
            pending = _subscribe(connection, event_types)
            captured = _capture(
                connection,
                pending,
                event_types=event_types,
                duration=args.duration,
                max_per_type=args.max_per_type,
            )

    if not captured and not state_events:
        raise SystemExit("no matching Home Assistant events were observed")
    sanitized = [
        *(
            sanitize_event(
                event,
                homeassistant_version=homeassistant_version,
                source="sanitized-live-state-snapshot",
            )
            for event in state_events
        ),
        *(sanitize_event(event, homeassistant_version=homeassistant_version) for event in captured),
    ]
    existing_fixtures = [load_fixture(path) for path in fixture_paths(args.output)]
    existing_shapes = {_fixture_shape(fixture) for fixture in existing_fixtures}
    new_shapes: set[tuple[object, ...]] = set()
    unique_fixtures: list[dict[str, Any]] = []
    for fixture in sanitized:
        shape = _fixture_shape(fixture)
        if shape in existing_shapes or shape in new_shapes:
            continue
        new_shapes.add(shape)
        unique_fixtures.append(fixture)
    sanitized = unique_fixtures
    if not sanitized:
        print("all observed Home Assistant event shapes already have fixtures")
        return
    per_type: Counter[str] = Counter(
        fixture["event"]["event_type"] for fixture in existing_fixtures
    )
    newly_written: Counter[str] = Counter()
    written: list[Path] = []
    for fixture in sanitized:
        event_type = fixture["event"]["event_type"]
        per_type[event_type] += 1
        newly_written[event_type] += 1
        path = args.output / _fixture_name(fixture, per_type[event_type])
        _write_fixture(path, fixture, overwrite=args.overwrite)
        written.append(path)

    summary = ", ".join(
        f"{event_type}={newly_written[event_type]}" for event_type in sorted(newly_written)
    )
    print(
        f"wrote {len(written)} sanitized fixtures from Home Assistant "
        f"{homeassistant_version}: {summary}"
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("capture interrupted", file=sys.stderr)
        raise SystemExit(130) from None
