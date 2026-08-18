.PHONY: sync lint test spike spike-openhands spike-langgraph integration

sync:
	uv sync --all-extras --group dev

lint:
	uv run ruff check .

test:
	uv run pytest -q

integration:
	uv run pytest -q -m integration

spike-langgraph:
	uv run python scripts/spike_langgraph.py

spike-openhands:
	uv sync --extra openhands --group dev
	uv run python scripts/spike_openhands.py

spike: spike-langgraph spike-openhands