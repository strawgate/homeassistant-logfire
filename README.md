# Home Assistant Logfire

Export Home Assistant health metrics, state changes, service calls, automation activity, and system log events directly to [Pydantic Logfire](https://pydantic.dev/logfire) using OpenTelemetry.

> [!WARNING]
> This integration is in active development and is not ready for installation yet.

## Design goals

- Use a dedicated Logfire project write token; never require an admin or read token.
- Keep private OpenTelemetry providers isolated from Home Assistant's global logging and telemetry providers.
- Make event handling non-blocking with a bounded, intentionally lossy queue.
- Emit a small, documented semantic model rather than copying arbitrary Home Assistant event payloads.
- Support clean config-entry reload and unload, redacted diagnostics, and token rotation.
- Preserve units in OTLP metric metadata instead of encoding units into metric names.

The first milestone is described in [docs/architecture.md](docs/architecture.md). The exporter review that informed it is in [docs/exporter-research.md](docs/exporter-research.md).

## Development

Python 3.14, `uv`, and `just` are required.

```console
just sync
just test-core
just test-integration
just check
```

`just test-core` creates a fresh temporary environment containing the project runtime and core
test dependencies only. Home Assistant is deliberately absent, and the suite asserts that it
cannot be imported. It tests pure event serialization, redaction, metric aggregation, bounded
lossy delivery, and a real gzip/protobuf OTLP round trip to an in-process HTTP receiver.

`just test-integration` adds Home Assistant and its custom-component test harness to verify the
config flow, event adapter, pipeline wiring, diagnostics, and config-entry lifecycle. `just test`
runs both layers.

## License

MIT
