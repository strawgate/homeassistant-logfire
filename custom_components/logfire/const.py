"""Constants for the Home Assistant Logfire integration."""

from typing import Final

DOMAIN: Final = "logfire"

CONF_ENVIRONMENT: Final = "environment"
CONF_EXCLUDE_ENTITIES: Final = "exclude_entities"
CONF_EXPORT_AUTOMATIONS: Final = "export_automations"
CONF_EXPORT_METRICS: Final = "export_metrics"
CONF_EXPORT_SERVICE_CALLS: Final = "export_service_calls"
CONF_EXPORT_STATE_CHANGES: Final = "export_state_changes"
CONF_EXPORT_SYSTEM_LOGS: Final = "export_system_logs"
CONF_INCLUDE_DOMAINS: Final = "include_domains"
CONF_METRIC_INTERVAL: Final = "metric_interval"
CONF_PROJECT_NAME: Final = "project_name"
CONF_PROJECT_URL: Final = "project_url"
CONF_QUEUE_SIZE: Final = "queue_size"
CONF_SERVICE_NAME: Final = "service_name"
CONF_TOKEN: Final = "token"

DEFAULT_ENVIRONMENT: Final = "home"
DEFAULT_EXPORT_AUTOMATIONS: Final = True
DEFAULT_EXPORT_METRICS: Final = True
DEFAULT_EXPORT_SERVICE_CALLS: Final = True
DEFAULT_EXPORT_STATE_CHANGES: Final = True
DEFAULT_EXPORT_SYSTEM_LOGS: Final = True
DEFAULT_METRIC_INTERVAL: Final = 60
DEFAULT_QUEUE_SIZE: Final = 2048
DEFAULT_SERVICE_NAME: Final = "home-assistant"

EVENT_AUTOMATION_TRIGGERED: Final = "automation_triggered"
EVENT_SYSTEM_LOG: Final = "system_log_event"

LOGFIRE_ENDPOINTS: Final = {
    "eu": "https://logfire-eu.pydantic.dev",
    "us": "https://logfire-us.pydantic.dev",
}

OTEL_SCOPE_NAME: Final = "strawgate.homeassistant.logfire"
OTEL_SCOPE_VERSION: Final = "0.1.0"

TOKEN_REDACT_KEYS: Final = {CONF_TOKEN}
