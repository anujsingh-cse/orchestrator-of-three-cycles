"""Deterministic fake LLM for tests, spikes, and CI (no API key needed)."""
from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

from orchestrator.adapters.llm.base import LLMAdapter, LLMResponse
from orchestrator.adapters.llm.pacing import Pacer


class FakeAdapter(LLMAdapter):
    """Contract-compliant fake: deterministic text, records every call.

    ``script`` lets tests inject behavior: callable(messages) -> str, or
    raise a specific exception (e.g. 429) to exercise the pacing path.
    """

    def __init__(
        self,
        model_id: str = "fake-model",
        script: object | None = None,
        *,
        pacer: Pacer | None = None,
    ) -> None:
        super().__init__(pacer)
        self.model_id = model_id
        self._script = script
        self.calls: list[list[dict[str, str]]] = []

    def stream(self, messages: list[dict[str, str]], **kwargs) -> Iterator[str]:
        self._pacer.acquire()
        self.calls.append(messages)
        try:
            text = self._render(messages)
        except Exception as exc:  # noqa: BLE001 - mirrors real adapters
            self._note_failure(exc)
            raise
        for token in text.split(" "):
            yield token + " "
        self._pacer.report_success()

    async def astream(self, messages: list[dict[str, str]], **kwargs) -> AsyncIterator[str]:
        self._pacer.acquire()
        self.calls.append(messages)
        try:
            text = self._render(messages)
        except Exception as exc:  # noqa: BLE001
            self._note_failure(exc)
            raise
        for token in text.split(" "):
            yield token + " "
        self._pacer.report_success()

    def complete(self, messages: list[dict[str, str]], **kwargs) -> LLMResponse:
        self._pacer.acquire()
        self.calls.append(messages)
        try:
            text = self._render(messages)
        except Exception as exc:  # noqa: BLE001
            self._note_failure(exc)
            raise
        self._pacer.report_success()
        return LLMResponse(
            text=text,
            finish_reason="stop",
            tokens_in=sum(len(m["content"].split()) for m in messages),
            tokens_out=len(text.split()),
            model_id=self.model_id,
        )

    def health(self) -> bool:
        return True

    def _note_failure(self, exc: Exception) -> None:
        status = getattr(exc, "status_code", None)
        if status == 429 or "429" in str(exc) or status == 503:
            self._pacer.report_rate_limited()

    def _render(self, messages: list[dict[str, str]]) -> str:
        if callable(self._script):
            result = self._script(messages)
            if isinstance(result, Exception):
                raise result
            return str(result)
        return f"[{self.model_id}] deterministic response to {len(messages)} messages"
