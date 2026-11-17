.PHONY: run test lint verify costs seed console

run:     ## start the call server on :7860
	uv run python -m app.server

console: ## start the private console API on 127.0.0.1:7861
	uv run python -m app.console_api

test:
	uv run pytest -q
	node --test tests/js/

lint:
	uv run ruff check .
	uv run ruff format --check .

verify: lint test

seed:    ## rebuild data/clinic.db from the static catalogue
	uv run python -m seed.build $(if $(ANCHOR),--anchor $(ANCHOR),)

costs:   ## cost and latency per call from data/calls/
	uv run python -m scripts.call_costs
