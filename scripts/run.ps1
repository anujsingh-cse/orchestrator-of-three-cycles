param(
    [ValidateSet("sync", "lint", "test", "integration", "spike", "spike-langgraph", "spike-openhands")]
    [string]$Target = "test"
)

switch ($Target) {
    "sync"           { uv sync --all-extras --group dev }
    "lint"           { uv run ruff check . }
    "test"           { uv run pytest -q }
    "integration"    { uv run pytest -q -m integration }
    "spike-langgraph" { uv run python scripts/spike_langgraph.py }
    "spike-openhands" { uv sync --extra openhands --group dev; uv run python scripts/spike_openhands.py }
    "spike"          { uv run python scripts/spike_langgraph.py; uv sync --extra openhands --group dev; uv run python scripts/spike_openhands.py }
}