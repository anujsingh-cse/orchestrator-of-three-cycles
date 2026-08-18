"""LLM adapter contract (P4 seam) with the D6 pacing discipline."""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass

from orchestrator.adapters.llm.pacing import Pacer


@dataclass(frozen=True)
class LLMResponse:
    text: str
    finish_reason: str
    tokens_in: int
    tokens_out: int
    model_id: str


class LLMAdapter(ABC):
    """Provider-agnostic LLM seam (P4) with the D6 pacing contract.

    - One interface; NIM and Ollama are drop-in implementations.
    - Streaming-first: ``stream`` / ``astream`` are the primary API (the TUI
      renders live token deltas).
    - Every request passes through a per-model :class:`Pacer`; repeated 429s
      raise :class:`CircuitOpenError` for the router to escalate (fallback to
      the draft model or a HITL prompt).
    - MUST NOT persist any state (D2): the audit DAG owns session state.
    """

    model_id: str

    def __init__(self, pacer: Pacer | None = None) -> None:
        self._pacer = pacer or Pacer()

    @abstractmethod
    def stream(self, messages: list[dict[str, str]], **kwargs) -> Iterator[str]:
        """Yield token deltas for a conversation."""

    @abstractmethod
    async def astream(self, messages: list[dict[str, str]], **kwargs) -> AsyncIterator[str]:
        """Async streaming variant."""

    @abstractmethod
    def complete(self, messages: list[dict[str, str]], **kwargs) -> LLMResponse:
        """Non-streaming fallback; same pacing path as ``stream``."""

    @abstractmethod
    def health(self) -> bool:
        """Cheap liveness probe for the router's circuit breaker."""
