"""Real OTLP/HTTP protobuf contract test against an in-process receiver."""

from __future__ import annotations

import gzip
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import (
    ExportLogsServiceRequest,
    ExportLogsServiceResponse,
)
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
    ExportMetricsServiceRequest,
    ExportMetricsServiceResponse,
)

from custom_components.logfire.core.model import TelemetryRecord
from custom_components.logfire.core.otlp import LogfireOtelClient, endpoint_for_token


@dataclass(slots=True)
class CapturedRequest:
    path: str
    authorization: str | None
    content_type: str | None
    content_encoding: str | None
    body: bytes


@dataclass(slots=True)
class Receiver:
    endpoint: str
    requests: list[CapturedRequest] = field(default_factory=list)


@pytest.fixture
def otlp_receiver() -> Iterator[Receiver]:
    receiver = Receiver(endpoint="")

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers["Content-Length"])
            body = self.rfile.read(length)
            receiver.requests.append(
                CapturedRequest(
                    path=self.path,
                    authorization=self.headers.get("Authorization"),
                    content_type=self.headers.get("Content-Type"),
                    content_encoding=self.headers.get("Content-Encoding"),
                    body=gzip.decompress(body),
                )
            )
            response = (
                ExportLogsServiceResponse()
                if self.path == "/v1/logs"
                else ExportMetricsServiceResponse()
            ).SerializeToString()
            self.send_response(200)
            self.send_header("Content-Type", "application/x-protobuf")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, format_: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    receiver.endpoint = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield receiver
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _attributes(items) -> dict[str, str]:
    return {item.key: item.value.string_value for item in items}


def test_endpoint_uses_region_encoded_in_write_token() -> None:
    assert endpoint_for_token("pylf_v1_eu_example") == "https://logfire-eu.pydantic.dev"
    assert endpoint_for_token("pylf_v1_us_example") == "https://logfire-us.pydantic.dev"
    assert endpoint_for_token("legacy-token") == "https://logfire-us.pydantic.dev"


def test_client_sends_gzipped_protobuf_logs_and_metrics(otlp_receiver: Receiver) -> None:
    client = LogfireOtelClient(
        token="pylf_v1_us_test-token",
        service_name="home-assistant-isolated-test",
        service_version="2026.8.3",
        service_instance_id="instance-id",
        environment="test",
        metric_export_interval=3600,
        queue_size=16,
        export_metrics=True,
        endpoint=otlp_receiver.endpoint,
    )
    gauge = client.meter.create_gauge(
        "homeassistant.entity.count",
        unit="1",
        description="Entities known to Home Assistant",
    )
    gauge.set(3, {"homeassistant.domain": "sensor"})
    client.emit(
        TelemetryRecord(
            event_name="homeassistant.state_changed",
            body="Home Assistant state changed",
            attributes={"homeassistant.entity.id": "sensor.temperature"},
            timestamp_ns=1_700_000_000_123_456_789,
        )
    )

    client.shutdown()
    client.shutdown()

    assert {request.path for request in otlp_receiver.requests} == {
        "/v1/logs",
        "/v1/metrics",
    }
    for request in otlp_receiver.requests:
        assert request.authorization == "pylf_v1_us_test-token"
        assert request.content_type == "application/x-protobuf"
        assert request.content_encoding == "gzip"

    log_capture = next(request for request in otlp_receiver.requests if request.path == "/v1/logs")
    logs = ExportLogsServiceRequest.FromString(log_capture.body)
    resource_logs = logs.resource_logs[0]
    resource_attributes = _attributes(resource_logs.resource.attributes)
    assert resource_attributes["service.name"] == "home-assistant-isolated-test"
    assert resource_attributes["service.namespace"] == "homeassistant"
    assert resource_attributes["service.instance.id"] == "instance-id"
    assert resource_attributes["deployment.environment.name"] == "test"
    scope_logs = resource_logs.scope_logs[0]
    assert scope_logs.scope.name == "strawgate.homeassistant.logfire"
    record = scope_logs.log_records[0]
    assert record.event_name == "homeassistant.state_changed"
    assert record.body.string_value == "Home Assistant state changed"
    assert record.time_unix_nano == 1_700_000_000_123_456_789
    assert _attributes(record.attributes) == {"homeassistant.entity.id": "sensor.temperature"}

    metric_capture = next(
        request for request in otlp_receiver.requests if request.path == "/v1/metrics"
    )
    metrics = ExportMetricsServiceRequest.FromString(metric_capture.body)
    scope_metrics = metrics.resource_metrics[0].scope_metrics[0]
    metric = next(
        item for item in scope_metrics.metrics if item.name == "homeassistant.entity.count"
    )
    assert metric.unit == "1"
    assert metric.description == "Entities known to Home Assistant"
    point = metric.gauge.data_points[0]
    assert point.as_int == 3
    assert _attributes(point.attributes) == {"homeassistant.domain": "sensor"}
