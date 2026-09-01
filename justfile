set shell := ["bash", "-cu"]

sync:
    uv sync --all-groups

test *args:
    just test-core {{args}}
    just test-integration {{args}}

test-core *args:
    uv run --isolated --no-default-groups --group core-test pytest \
      --cov=custom_components.logfire.core --cov-report=term-missing tests/core {{args}}

test-integration *args:
    uv run --no-default-groups --group core-test --group homeassistant-test pytest \
      --cov=custom_components.logfire --cov-report=term-missing tests/test_*.py {{args}}

lint:
    uv run --isolated --no-default-groups --group lint ruff check .
    uv run --isolated --no-default-groups --group lint ruff format --check .

format:
    uv run ruff check --fix .
    uv run ruff format .

check:
    just lint
    just test
