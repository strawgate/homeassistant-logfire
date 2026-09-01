# Sanitized Home Assistant event fixtures

## Why this is not a VCR

HTTP VCR cassettes are a poor fit for Home Assistant's event WebSocket. They preserve protocol
frames, authentication exchanges, timing noise, and potentially sensitive arbitrary attributes,
while tests only need representative event payloads. This repository instead keeps a small,
reviewable replay corpus derived from real payloads.

The fixture boundary is deliberately narrow:

- Capture is read-only: it uses `subscribe_events` and `get_states` only.
- The access token is accepted from the environment, never from a command-line argument.
- Raw messages remain in memory and are never written to a temporary capture file.
- Only supported event types and allowlisted state metadata survive sanitization.
- Entity, context, and user identifiers are replaced with deterministic generic identifiers.
- Timestamps are normalized, arbitrary string states become `sample`, and arbitrary attributes and
  service data are discarded.
- A second safety scan rejects secret-like keys, credentials, URLs, IPv4 addresses, MAC addresses,
  email addresses, oversized strings, and identifiers that do not match the pseudonym schema.
- Fixture writes use a temporary file and atomic replacement only after sanitization succeeds.

Each fixture records whether it came from a live WebSocket event or a read-only state snapshot. A
state snapshot uses the same state dictionary Home Assistant embeds in `state_changed`, but it is
marked `sanitized-live-state-snapshot` rather than being presented as an observed event.

## Test contract

`just fixture-check` validates the schema and privacy rules without installing Home Assistant.
`just test-integration` then:

1. Loads every checked-in fixture through the same validator.
2. Reconstructs Home Assistant `State`, `Context`, and `Event` objects.
3. Runs the real integration adapter and framework-independent serializer.
4. Verifies state values and semantic metadata survive while user IDs do not.

Hand-authored unit tests still cover deliberately rare cases such as service calls, automation
events, system logs, malformed payloads, and injected credentials. A rare event is not labeled as a
live sample unless it was actually observed.

## Refreshing after a Home Assistant upgrade

Use a dedicated read-only Home Assistant token and pass it via the environment or an encrypted
secret wrapper. The repository's capture commands never print it:

```console
HOMEASSISTANT_URL=https://homeassistant.example:8123 \
HOMEASSISTANT_TOKEN=... \
just capture-homeassistant-states
```

To observe selected event-bus traffic for up to 30 seconds:

```console
HOMEASSISTANT_URL=https://homeassistant.example:8123 \
HOMEASSISTANT_TOKEN=... \
just capture-homeassistant-events
```

The command skips event shapes already represented in the corpus and refuses to overwrite files by
default. TLS certificate verification is enabled by default; for a local instance whose certificate
cannot currently be validated, explicitly set `HOMEASSISTANT_TLS_VERIFY=false` in the encrypted
capture environment. After capture:

1. Review every new JSON file manually. Sanitization is defense in depth, not a substitute for
   review before publication.
2. Run `just check`.
3. Confirm the recorded `homeassistant_version` matches the instance used for capture.
4. Commit only the sanitized JSON; never add a raw WebSocket transcript or token.

The capture protocol follows Home Assistant's
[WebSocket API](https://developers.home-assistant.io/docs/api/websocket/) and the fixture replay uses
the public [`State.from_dict`](https://developers.home-assistant.io/docs/dev_101_states/) shape.
