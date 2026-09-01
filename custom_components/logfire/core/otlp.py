"""Private OpenTelemetry providers that export directly to Logfire."""

from __future__ import annotations

import re
import time
from typing import Final

from opentelemetry._logs import Logger
from opentelemetry.exporter.otlp.proto.http import Compression
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.metrics import Meter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource

from ..const import LOGFIRE_ENDPOINTS, OTEL_SCOPE_NAME, OTEL_SCOPE_VERSION
from .model import TelemetryRecord

_TOKEN_REGION_PATTERN: Final = re.compile(r"^pylf_v[0-9]+_(?P<region>[a-z]+)_")


def endpoint_for_token(token: str) -> str:
    """Return the Logfire data-region endpoint encoded in a write token."""
    if match := _TOKEN_REGION_PATTERN.match(token):
        region = match.group("region")
        if region in LOGFIRE_ENDPOINTS:
            return LOGFIRE_ENDPOINTS[region]
    return LOGFIRE_ENDPOINTS["us"]


class LogfireOtelClient:
    """Own private OTel providers and their complete lifecycle."""

    def __init__(
        self,
        *,
        token: str,
        service_name: str,
        service_version: str,
        service_instance_id: str,
        environment: str,
        metric_export_interval: int,
        queue_size: int,
        export_metrics: bool,
        endpoint: str | None = None,
    ) -> None:
        """Create private log and metric providers for one config entry."""
        base_endpoint = (endpoint or endpoint_for_token(token)).rstrip("/")
        headers = {"Authorization": token}
        resource = Resource.create(
            {
                "deployment.environment.name": environment,
                "homeassistant.version": service_version,
                "service.instance.id": service_instance_id,
                "service.name": service_name,
                "service.namespace": "homeassistant",
                "service.version": service_version,
            }
        )

        log_exporter = OTLPLogExporter(
            endpoint=f"{base_endpoint}/v1/logs",
            headers=headers,
            timeout=10,
            compression=Compression.Gzip,
        )
        self._logger_provider = LoggerProvider(resource=resource)
        self._logger_provider.add_log_record_processor(
            BatchLogRecordProcessor(
                log_exporter,
                schedule_delay_millis=1000,
                max_export_batch_size=min(512, queue_size),
                export_timeout_millis=10_000,
                max_queue_size=queue_size,
            )
        )
        self.logger: Logger = self._logger_provider.get_logger(
            OTEL_SCOPE_NAME,
            OTEL_SCOPE_VERSION,
        )

        metric_readers = []
        if export_metrics:
            metric_exporter = OTLPMetricExporter(
                endpoint=f"{base_endpoint}/v1/metrics",
                headers=headers,
                timeout=10,
                compression=Compression.Gzip,
            )
            metric_readers.append(
                PeriodicExportingMetricReader(
                    metric_exporter,
                    export_interval_millis=metric_export_interval * 1000,
                    export_timeout_millis=10_000,
                )
            )
        self._meter_provider = MeterProvider(resource=resource, metric_readers=metric_readers)
        self.meter: Meter = self._meter_provider.get_meter(
            OTEL_SCOPE_NAME,
            OTEL_SCOPE_VERSION,
        )
        self._is_shutdown = False

    def emit(self, record: TelemetryRecord) -> None:
        """Emit one record to the private provider's bounded batch processor."""
        self.logger.emit(
            timestamp=record.timestamp_ns,
            observed_timestamp=time.time_ns(),
            severity_number=record.severity_number,
            severity_text=record.severity_text,
            body=record.body,
            attributes=record.attributes,
            event_name=record.event_name,
        )

    def force_flush(self, timeout_millis: int = 3000) -> bool:
        """Flush both signals within a shared bounded best-effort window."""
        logs_flushed = self._logger_provider.force_flush(timeout_millis)
        metrics_flushed = self._meter_provider.force_flush(timeout_millis)
        return logs_flushed and metrics_flushed

    def shutdown(self, timeout_millis: int = 3000) -> None:
        """Flush and close providers within a bounded best-effort window."""
        if self._is_shutdown:
            return
        self._is_shutdown = True
        try:
            self.force_flush(timeout_millis)
        finally:
            try:
                self._logger_provider.shutdown()
            finally:
                self._meter_provider.shutdown(timeout_millis)
