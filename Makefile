.PHONY: all install dev lint format fix mypy check test clean

all: check test

install:
	uv pip install -e .

dev:
	uv pip install -e ".[dev]"

lint:
	uv run ruff check .

fix:
	uv run ruff check . --fix
	uv run ruff format .

format:
	uv run ruff format .

mypy:
	uv run mypy .

test:
	uv run pytest -v

check: lint mypy
	uv run ruff format --check .

clean:
	rm -rf dist/
	rm -rf build/
	rm -rf .pytest_cache/
	rm -rf .mypy_cache/
	rm -rf .ruff_cache/
	rm -rf *.egg-info/
	rm -rf bytegate/_version.py
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
