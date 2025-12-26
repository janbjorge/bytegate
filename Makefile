.PHONY: all lint format test mypy check build clean clean-all install dev

all: check test

# Install dependencies
install:
	pip install -e .

# Install with dev dependencies
dev:
	pip install -e ".[dev]"

# Run all linters
lint:
	ruff check .

# Auto-fix linting issues
fix:
	ruff check . --fix
	ruff format .

# Format code
format:
	ruff format .

# Type checking
mypy:
	mypy .

# Run tests
test:
	pytest -v

# Run tests with coverage
test-cov:
	pytest --cov=bytegate --cov-report=term-missing

# Run all checks (lint + mypy + format check)
check: lint mypy
	ruff format --check .

# Build package
build:
	python -m build

# Clean build artifacts (preserves .venv)
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

# Clean everything including .venv
clean-all: clean
	rm -rf .venv/

# Publish to PyPI (requires credentials)
publish: clean build
	python -m twine upload dist/*

# Publish to TestPyPI
publish-test: clean build
	python -m twine upload --repository testpypi dist/*
