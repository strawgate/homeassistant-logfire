"""Logfire token validation plus compatibility exports for the OTLP core."""

from __future__ import annotations

from dataclasses import dataclass

from aiohttp import ClientError, ClientSession, ClientTimeout

from .core.otlp import LogfireOtelClient, endpoint_for_token

__all__ = [
    "CannotConnectError",
    "InvalidAuthError",
    "LogfireOtelClient",
    "ProjectInfo",
    "async_validate_token",
    "endpoint_for_token",
]


class CannotConnectError(Exception):
    """Raised when the Logfire API cannot be reached."""


class InvalidAuthError(Exception):
    """Raised when a Logfire token is rejected."""


@dataclass(frozen=True, slots=True)
class ProjectInfo:
    """Safe project metadata returned by Logfire."""

    name: str
    url: str


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
    except (ClientError, TimeoutError, ValueError, TypeError) as error:
        raise CannotConnectError from error

    project_name = data.get("project_name")
    project_url = data.get("project_url")
    if not isinstance(project_name, str) or not isinstance(project_url, str):
        raise CannotConnectError
    return ProjectInfo(name=project_name, url=project_url)
