.PHONY: run test lint verify costs

run:     ## start the call server on :7860
	uv run python -m app.server

test:
	uv run pytest -q
	node --test tests/js/

lint:
	uv run ruff check .
	uv run ruff format --check .

verify: lint test

costs:   ## cost and latency per call from data/calls/
	uv run python -m scripts.call_costs
