"""Private OpenTelemetry providers that export directly to Logfire."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Final

from aiohttp import ClientError, ClientSession, ClientTimeout
from opentelemetry._logs import Logger, SeverityNumber
from opentelemetry.exporter.otlp.proto.http import Compression
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.metrics import Meter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource

from .const import LOGFIRE_ENDPOINTS, OTEL_SCOPE_NAME, OTEL_SCOPE_VERSION

_TOKEN_REGION_PATTERN: Final = re.compile(r"^pylf_v[0-9]+_(?P<region>[a-z]+)_")


class CannotConnectError(Exception):
    """Raised when the Logfire API cannot be reached."""


class InvalidAuthError(Exception):
    """Raised when a Logfire token is rejected."""


@dataclass(frozen=True, slots=True)
class ProjectInfo:
    """Safe project metadata returned by Logfire."""

    name: str
    url: str


@dataclass(frozen=True, slots=True)
class TelemetryRecord:
    """A minimized Home Assistant event ready for OTLP emission."""

    event_name: str
    body: str
    attributes: dict[str, Any]
    timestamp_ns: int
    severity_number: SeverityNumber = SeverityNumber.INFO
    severity_text: str = "INFO"


def endpoint_for_token(token: str) -> str:
    """Return the Logfire data-region endpoint encoded in a write token."""
    if match := _TOKEN_REGION_PATTERN.match(token):
        region = match.group("region")
        if region in LOGFIRE_ENDPOINTS:
            return LOGFIRE_ENDPOINTS[region]
    return LOGFIRE_ENDPOINTS["us"]


async def async_validate_token(session: ClientSession, token: str) -> ProjectInfo:
    """Validate a project write token without exporting telemetry."""
    endpoint = endpoint_for_token(token)
    try:
        async with session.get(
            f"{endpoint}/v1/info",
            headers={"Authorization": token},
            timeout=ClientTimeout(total=10),
        ) as response:
            if response.status in (401, 403):
                raise InvalidAuthError
            if response.status != 200:
                raise CannotConnectError
            data = await response.json()
    except InvalidAuthError:
        raise
    except (ClientError, TimeoutError, ValueError, TypeError) as err:
        raise CannotConnectError from err

    project_name = data.get("project_name")
    project_url = data.get("project_url")
    if not isinstance(project_name, str) or not isinstance(project_url, str):
        raise CannotConnectError
    return ProjectInfo(name=project_name, url=project_url)


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
    ) -> None:
        """Create private log and metric providers for one config entry."""
        endpoint = endpoint_for_token(token)
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
            endpoint=f"{endpoint}/v1/logs",
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
                endpoint=f"{endpoint}/v1/metrics",
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
        self._meter_provider = MeterProvider(
            resource=resource,
            metric_readers=metric_readers,
        )
        self.meter: Meter = self._meter_provider.get_meter(
            OTEL_SCOPE_NAME,
            OTEL_SCOPE_VERSION,
        )

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

    def shutdown(self, timeout_millis: int = 3000) -> None:
        """Flush and close providers within a bounded best-effort window."""
        self._logger_provider.force_flush(timeout_millis)
        self._meter_provider.force_flush(timeout_millis)
        self._logger_provider.shutdown()
        self._meter_provider.shutdown(timeout_millis)
