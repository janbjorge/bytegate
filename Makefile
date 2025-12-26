.PHONY: all lint format test mypy check build clean install dev

all: check test

# Install dependencies
install:
	uv sync

# Install with dev dependencies
dev:
	uv sync --all-extras

# Run all linters
lint:
	uv run ruff check .

# Auto-fix linting issues
fix:
	uv run ruff check . --fix
	uv run ruff format .

# Format code
format:
	uv run ruff format .

# Type checking
mypy:
	uv run mypy .

# Run tests
test:
	uv run pytest -v

# Run tests with coverage
test-cov:
	uv run pytest --cov=bytegate --cov-report=term-missing

# Run all checks (lint + mypy + format check)
check: lint mypy
	uv run ruff format --check .

# Build package
build:
	uv build

# Clean build artifacts
clean:
	rm -rf dist/
	rm -rf .venv/
	rm -rf .pytest_cache/
	rm -rf .mypy_cache/
	rm -rf .ruff_cache/
	rm -rf *.egg-info/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true

# Publish to PyPI (requires credentials)
publish: clean build
	uv publish

# Publish to TestPyPI
publish-test: clean build
	uv publish --index-url https://test.pypi.org/legacy/
