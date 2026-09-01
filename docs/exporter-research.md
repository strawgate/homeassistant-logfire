# Exporter research

## Summary

The first implementation will use private OpenTelemetry providers that send directly to Logfire. They provide OTLP logs and metrics, gzip, batching, bounded SDK queues, retry handling, and lifecycle methods without replacing Home Assistant's global telemetry providers.

The Home Assistant integration owns event selection, privacy boundaries, event-loop-safe metric collection, and a small bounded queue. It deliberately does not forward arbitrary event bodies.

## Candidates

### First-party Logfire SDK

`logfire.configure(local=True, ...)` creates private telemetry providers and gives the best native Logfire record model, batching, gzip, retry, scrubbing, `force_flush`, and `shutdown`.

However, Logfire 4.41.0 also instruments process executors and updates the process-global OpenTelemetry propagator while initializing a local configuration. Those side effects are appropriate for an application choosing Logfire as its telemetry SDK, but too invasive for a dynamically loaded Home Assistant custom integration.

### Standard OpenTelemetry Python SDK: selected

Private `LoggerProvider` and `MeterProvider` instances preserve the OTLP data model and send directly to Logfire with HTTP/protobuf, gzip, batch processors, standard retry behavior, explicit resources, `force_flush`, and `shutdown`. They do not need to be installed globally. We must own privacy filtering and Logfire-oriented record conventions, which is a reasonable trade because Home Assistant event data needs explicit minimization anyway.

Observable metric callbacks run on SDK threads, so this integration records synchronous gauges from the Home Assistant event loop instead of reading Home Assistant registries from a background callback.

### Hand-written OTLP payloads

`remote_logger` demonstrates that direct JSON and protobuf encoding can avoid SDK dependencies. It also makes the integration responsible for protocol evolution, partial success handling, size limits, retry semantics, serialization, and exporter lifecycle. That is not a good reliability trade for this project.

## Patterns to adopt

- From `homeassistant-elasticsearch`: separate listen, filter, format, and publish concerns; support config-entry migrations, reauthentication, diagnostics, and entity/device/area/label-aware filtering.
- From Home Assistant Prometheus: listen for registry changes, clean up stale label sets, expose availability independently from numeric state, and use domain-specific state conversion.
- From Home Assistant InfluxDB and event-stream integrations: keep callbacks cheap, execute blocking clients off the event loop, and make unload remove every listener and task.
- From `remote_logger`: prevent exporter-log feedback loops, expose batch health, validate the endpoint before saving configuration, and cap serialized data.
- From OpenTelemetry: use OTLP/HTTP protobuf, gzip, timeouts, and exponential backoff with jitter; keep metric units in instrument metadata.
- From Logfire: use a project-scoped write token, a named service, deployment environment, and direct OTLP ingestion.

## Pitfalls

- A single `homeassistant.entity.state` gauge cannot safely mix temperature, power, energy, percentages, and unitless values. Typed entity measurements need one instrument per semantic measurement and UCUM unit.
- Point-in-time Home Assistant events are not traces. Service-call, automation, and state-change events should be records until a public start/completion boundary exists.
- Full event bodies can contain credentials, user IDs, location data, camera URLs, and unbounded nested attributes.
- Root Python logging can create an export-failure feedback loop. The first milestone consumes Home Assistant's `system_log_event` and explicitly drops exporter loggers.
- A config-entry password selector prevents display in the UI, but Home Assistant config-entry storage is not a separate encrypted vault. Appliance access controls and encrypted backups remain the security boundary.
- An unbounded application queue turns an observability outage into a Home Assistant memory outage. The queue must drop telemetry under sustained pressure and report the drop count.

## Actionable items

1. Ship token validation, reauthentication, reconfiguration, and redacted diagnostics.
2. Export lifecycle, state, service, automation, and system log records through a bounded queue.
3. Export low-cardinality health metrics from the Home Assistant event loop.
4. Define and test typed entity measurement mappings before enabling numeric entity metrics.
5. Add area, device, label, domain, and entity filters using Home Assistant selectors.
6. Add a Logfire dashboard and alert pack only after live telemetry confirms the schema.

The direct-ingestion smoke test was completed on 2026-09-01 with the project write token: Logfire accepted an OTLP log with its event name and resource attributes, plus a gauge with unit `1`, description, scope, resource, and data-point attributes. The admin token was not used for ingestion.

## Sources

- [Home Assistant Elasticsearch integration](https://github.com/strawgate/homeassistant-elasticsearch)
- [Home Assistant Prometheus integration](https://github.com/home-assistant/core/tree/2026.8.3/homeassistant/components/prometheus)
- [Home Assistant InfluxDB integration](https://github.com/home-assistant/core/tree/2026.8.3/homeassistant/components/influxdb)
- [Remote Logger 2.0.3](https://github.com/rhizomatics/remote_logger/tree/v2.0.3)
- [Home Assistant OpenTelemetry 0.4.0](https://github.com/cedricziel/ha-opentelemetry/tree/v0.4.0)
- [Logfire Python SDK 4.41.0](https://github.com/pydantic/logfire/tree/v4.41.0)
- [Logfire alternative-client OTLP configuration](https://pydantic.dev/docs/logfire/guides/alternative-clients/)
- [OpenTelemetry Python exporters](https://opentelemetry.io/docs/languages/python/exporters/)
- [OpenTelemetry OTLP exporter specification](https://opentelemetry.io/docs/specs/otel/protocol/exporter/)
- [OpenTelemetry metric semantic conventions](https://opentelemetry.io/docs/specs/semconv/general/metrics/)
- [Home Assistant config flows](https://developers.home-assistant.io/docs/core/integration/config_flow/)
- [Home Assistant diagnostics rule](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/diagnostics/)
