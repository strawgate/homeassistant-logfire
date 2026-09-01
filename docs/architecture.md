# Architecture

## First milestone

```text
Home Assistant event bus       Home Assistant registries
          |                              |
          +--------- thin adapter -------+
                          |
                   plain snapshots
                          |
                          v
       Home Assistant-independent exporter core
       - allowlisted serializers and redaction
       - health-metric aggregation
       - bounded lossy delivery queue
       - private OpenTelemetry SDK providers
                          |
                 OTLP/HTTP protobuf + gzip
                          |
                          v
                       Logfire
```

The integration is a HACS custom integration, not a Home Assistant add-on. It runs inside Home Assistant so it can use the event bus and registries without a second credential or polling API.

The `custom_components.logfire.core` package imports no Home Assistant modules. The adapter copies
only allowlisted fields into frozen plain snapshots before the core sees an event. This makes the
privacy boundary and most exporter behavior deterministic and testable without starting or even
installing Home Assistant.

## Security model

- The only credential is a dedicated Logfire project write token.
- The token is entered with a password selector and stored in config-entry data.
- Diagnostics redact the token and never expose its prefix or length.
- Reauthentication replaces only the token; reconfiguration may rotate it while preserving other settings.
- No Logfire admin key, read token, or Home Assistant long-lived access token is needed.
- Full arbitrary Home Assistant event payloads are not exported.
- Context correlation IDs may be exported; Home Assistant user IDs are not.
- Allowlisted serializers are the privacy boundary; no generic payload serializer is used.

Home Assistant config entries are not encrypted independently on disk. Encrypted full backups protect the token at rest outside the appliance, while filesystem and administrative access protect it on the running appliance.

## Delivery model

Event callbacks serialize a bounded set of scalar fields and call `put_nowait` on a bounded queue. If the queue is full, the new record is discarded and `homeassistant.telemetry.dropped` is incremented. Home automation must never wait for telemetry delivery.

A config-entry-owned background task drains the queue into a private OpenTelemetry logger provider. Private batch processors provide compression, bounded queues, and transient retry. Unload removes listeners, stops metric scheduling, attempts a short queue drain, cancels the worker, and shuts down the providers off the event loop.

The queue worker treats SDK exceptions as record loss, records the failure, and continues with the
next item. Adapter-owned outcome observers are also isolated so a telemetry counter or logging
failure cannot break event production or terminate delivery.

## Initial records

All records set the OpenTelemetry `EventName`. Logfire exposes that value as `span_name` in the `records` table. The exporter also supplies `event.name`; Logfire normalizes that duplicate attribute away during ingestion.

| Event | Exported fields |
| --- | --- |
| `state_changed` | entity ID/domain, old/new state, unit, device class, state class, area/device IDs, context IDs |
| `call_service` | service domain/name, filtered target entity IDs, context IDs |
| `automation_triggered` | automation entity ID/name, context IDs |
| `system_log_event` | level, logger, source, bounded message; exporter loggers are dropped |
| lifecycle | event name and timestamp |

No arbitrary state attributes, service data, exception bodies, or user IDs are included.

## Initial metrics

| Metric | Unit | Attributes |
| --- | --- | --- |
| `homeassistant.entity.count` | `1` | domain |
| `homeassistant.entity.unavailable.count` | `1` | domain |
| `homeassistant.config_entry.count` | `1` | domain, state |
| `homeassistant.integration.setup.duration` | `s` | domain |
| `homeassistant.telemetry.event` | `1` | event name |
| `homeassistant.telemetry.dropped` | `1` | reason |

The first milestone intentionally excludes numeric entity values. The next milestone will map Home Assistant device/state classes and canonical units to typed instruments instead of placing heterogeneous values into one gauge.

## Testability boundary

The test pyramid has two separate dependency environments:

- `just test-core` uses `uv run --isolated` with only OpenTelemetry and core test dependencies. It
  asserts Home Assistant is unavailable, then covers the pure schema, redaction, filtering,
  aggregation, and bounded-delivery behavior.
- The core protocol test starts a loopback OTLP/HTTP receiver, accepts `/v1/logs` and `/v1/metrics`,
  decompresses the gzip bodies, and decodes the official protobuf request types. It asserts paths,
  headers, resources, instrumentation scope, record fields, and metric points.
- `just test-integration` adds Home Assistant and verifies the thin adapter, config flow, lifecycle,
  and diagnostics.

This split follows uv's isolated-run and dependency-group model and the OTLP specification's
binary protobuf paths and gzip requirements.

## Resource attributes

- `service.name`: configurable, default `home-assistant`
- `service.namespace`: `homeassistant`
- `service.version`: current Home Assistant version
- `service.instance.id`: Home Assistant instance ID
- `deployment.environment.name`: configurable, default `home`
- `homeassistant.version`: current Home Assistant version

## Configuration lifecycle

The config flow validates the write token with the Logfire `/v1/info` endpoint before storing it. `401` and `403` are authentication errors; connection and server failures are retryable setup failures. Config-entry options control event groups, metric sampling, filters, and queue size. Any data or option change reloads the entry.
