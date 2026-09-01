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

capture-homeassistant-events output="tests/fixtures/homeassistant" duration="30" max_per_type="3":
    uv run --isolated --no-default-groups --group fixture-tools \
      python -m tools.capture_homeassistant_events \
      --output "{{output}}" --duration "{{duration}}" --max-per-type "{{max_per_type}}"

capture-homeassistant-states output="tests/fixtures/homeassistant" count="8":
    uv run --isolated --no-default-groups --group fixture-tools \
      python -m tools.capture_homeassistant_events \
      --output "{{output}}" --state-snapshot-count "{{count}}" --no-live-events

fixture-check:
    uv run --isolated --no-default-groups --group core-test \
      python -m tools.event_fixtures tests/fixtures/homeassistant

lint:
    uv run --isolated --no-default-groups --group lint ruff check .
    uv run --isolated --no-default-groups --group lint ruff format --check .

format:
    uv run ruff check --fix .
    uv run ruff format .

check:
    just lint
    just fixture-check
    just test
