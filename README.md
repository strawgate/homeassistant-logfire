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
just check
```

## License

MIT
