set shell := ["zsh", "-cu"]

sync:
    uv sync

test *args:
    uv run pytest {{args}}

lint:
    uv run ruff check .
    uv run ruff format --check .

format:
    uv run ruff check --fix .
    uv run ruff format .

check:
    just lint
    just test
