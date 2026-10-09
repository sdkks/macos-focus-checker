.DEFAULT_GOAL := help
.PHONY: help setup install-hooks test lint format format-check check run

help:
	@echo 'make setup         Create/reuse Python 3.14 venv, install requirements and hooks'
	@echo 'make install-hooks Install pre-commit and commit-msg hooks using existing venv'
	@echo 'make test          Run deterministic unittest tests (no native monitor)'
	@echo 'make lint          Run Ruff lint/import checks'
	@echo 'make format        Apply Ruff formatting'
	@echo 'make format-check  Check formatting without changing files'
	@echo 'make check         Run lint, format-check and tests'
	@echo 'make run           Start the monitor (macOS; writes a private log)'

setup:
	@if [ -e venv ] || [ -L venv ]; then \
		venv/bin/python -c 'import sys; sys.exit(not (sys.version_info[:2] == (3, 14) and sys.prefix != sys.base_prefix))' || \
		{ echo 'setup requires a Python 3.14 virtual environment; existing venv left unchanged. See README.md.' >&2; exit 1; }; \
	else \
		uv venv venv --python 3.14 --seed || exit 1; \
	fi
	venv/bin/python -m pip install -r requirements.txt
	$(MAKE) install-hooks
	@echo 'direnv is optional; inspect any local .envrc yourself before allowing it.'

install-hooks:
	@venv/bin/python -m pre_commit --version >/dev/null || \
		{ echo 'Install the pinned developer tools in venv first; see README.md.' >&2; exit 1; }
	venv/bin/python -m pre_commit install --hook-type pre-commit --hook-type commit-msg

test:
	venv/bin/python -B -m unittest discover -v

lint:
	venv/bin/python -m ruff check .

format:
	venv/bin/python -m ruff format .

format-check:
	venv/bin/python -m ruff format --check .

check: lint format-check test

run:
	venv/bin/python main.py
