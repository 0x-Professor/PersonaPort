.PHONY: install lint test check build

install:
	python -m pip install -e .[dev]

lint:
	ruff check .

test:
	pytest

check: lint test

build:
	python -m build
