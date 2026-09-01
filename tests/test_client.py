"""Tests for Logfire connection validation and region routing."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.logfire.client import (
    InvalidAuthError,
    async_validate_token,
    endpoint_for_token,
)


def test_endpoint_for_token() -> None:
    assert endpoint_for_token("pylf_v1_eu_example") == "https://logfire-eu.pydantic.dev"
    assert endpoint_for_token("pylf_v1_us_example") == "https://logfire-us.pydantic.dev"
    assert endpoint_for_token("legacy-token") == "https://logfire-us.pydantic.dev"


async def test_validate_token_returns_safe_project_metadata() -> None:
    response = MagicMock(status=200)
    response.json = AsyncMock(
        return_value={
            "project_name": "home-assistant",
            "project_url": "https://logfire.pydantic.dev/example/home-assistant",
        }
    )
    context_manager = AsyncMock()
    context_manager.__aenter__.return_value = response
    session = MagicMock()
    session.get.return_value = context_manager

    project = await async_validate_token(session, "pylf_v1_us_testtoken")

    assert project.name == "home-assistant"
    assert project.url.endswith("/home-assistant")
    assert session.get.call_args.kwargs["headers"] == {"Authorization": "pylf_v1_us_testtoken"}


async def test_validate_token_rejects_auth_failure() -> None:
    response = MagicMock(status=401)
    context_manager = AsyncMock()
    context_manager.__aenter__.return_value = response
    session = MagicMock()
    session.get.return_value = context_manager

    with pytest.raises(InvalidAuthError):
        await async_validate_token(session, "bad-token")
