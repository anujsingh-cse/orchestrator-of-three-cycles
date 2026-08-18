"""LLM adapter contract tests (D6) against the deterministic FakeAdapter."""

from __future__ import annotations

import pytest

from orchestrator.adapters.llm.base import LLMResponse
from orchestrator.adapters.llm.fake import FakeAdapter
from orchestrator.adapters.llm.pacing import CircuitOpenError, Pacer, PacingConfig


def test_complete_returns_contract_response() -> None:
    adapter = FakeAdapter("fake-model")
    response = adapter.complete([{"role": "user", "content": "hello"}])
    assert isinstance(response, LLMResponse)
    assert response.model_id == "fake-model"
    assert response.text.startswith("[fake-model]")


def test_stream_yields_deltas() -> None:
    adapter = FakeAdapter("fake-model")
    tokens = list(adapter.stream([{"role": "user", "content": "hello"}]))
    assert len(tokens) > 0
    assert all(isinstance(t, str) for t in tokens)


def test_scripted_429_trips_circuit_breaker() -> None:
    class RateLimited(Exception):
        status_code = 429

    def script(messages: list[dict[str, str]]) -> Exception:
        return RateLimited("rate limited")

    pacer = Pacer(PacingConfig(circuit_open_after_failures=3, base_backoff_seconds=0.01))
    adapter = FakeAdapter("fake-model", script=script, pacer=pacer)
    for _ in range(3):
        with pytest.raises(RateLimited):
            adapter.complete([{"role": "user", "content": "x"}])
    with pytest.raises(CircuitOpenError):
        adapter.complete([{"role": "user", "content": "x"}])
