"""Ollama drop-in implementation (P4: one-line swap, zero cost by design)."""
from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

from orchestrator.adapters.llm.base import LLMAdapter, LLMResponse
from orchestrator.adapters.llm.pacing import Pacer

try:  # optional extra: uv sync --extra ollama
    from ollama import AsyncClient, Client
except ImportError:  # pragma: no cover - exercised at runtime
    Client = None
    AsyncClient = None


class OllamaAdapter(LLMAdapter):
    """Local fallback / draft model (candidate ``qwen3:8b``, D22 probe).

    Implements the same contract as NIMAdapter so the router can swap
    providers without touching graph code.
    """

    def __init__(
        self,
        model_id: str = "qwen3:8b",
        *,
        host: str = "http://localhost:11434",
        pacer: Pacer | None = None,
    ) -> None:
        super().__init__(pacer)
        if Client is None:
            raise RuntimeError("install the ollama extra: uv sync --extra ollama")
        self.model_id = model_id
        self._client = Client(host=host)
        self._aclient = AsyncClient(host=host)

    def stream(self, messages: list[dict[str, str]], **kwargs) -> Iterator[str]:
        self._pacer.acquire()
        try:
            stream = self._client.chat(
                model=self.model_id, messages=messages, stream=True, **kwargs
            )
            for chunk in stream:
                if chunk.message and chunk.message.content:
                    yield chunk.message.content
            self._pacer.report_success()
        except Exception as exc:  # noqa: BLE001
            self._note_failure(exc)
            raise

    async def astream(self, messages: list[dict[str, str]], **kwargs) -> AsyncIterator[str]:
        self._pacer.acquire()
        try:
            stream = self._aclient.chat(
                model=self.model_id, messages=messages, stream=True, **kwargs
            )
            async for chunk in stream:
                if chunk.message and chunk.message.content:
                    yield chunk.message.content
            self._pacer.report_success()
        except Exception as exc:  # noqa: BLE001
            self._note_failure(exc)
            raise

    def complete(self, messages: list[dict[str, str]], **kwargs) -> LLMResponse:
        self._pacer.acquire()
        try:
            result = self._client.chat(model=self.model_id, messages=messages, **kwargs)
            self._pacer.report_success()
            usage = result.get("prompt_eval_count", 0), result.get("eval_count", 0)
            return LLMResponse(
                text=str(result.message.content),
                finish_reason="stop",
                tokens_in=int(usage[0]),
                tokens_out=int(usage[1]),
                model_id=self.model_id,
            )
        except Exception as exc:  # noqa: BLE001
            self._note_failure(exc)
            raise

    def health(self) -> bool:
        try:
            return bool(self._client.list() and self._client.show(self.model_id))
        except Exception:  # noqa: BLE001
            return False

    def _note_failure(self, exc: Exception) -> None:
        if getattr(exc, "status_code", None) == 429:
            self._pacer.report_rate_limited()
