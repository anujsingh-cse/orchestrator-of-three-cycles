"""NVIDIA NIM implementation of the LLM adapter (zero-cost free tier, P4)."""
from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator

from orchestrator.adapters.llm.base import LLMAdapter, LLMResponse
from orchestrator.adapters.llm.pacing import Pacer
from orchestrator.roster import NIM_TEMPLATE_KWARGS

try:  # optional extra: uv sync --extra nim
    from langchain_nvidia_ai_endpoints import ChatNVIDIA
except ImportError:  # pragma: no cover - exercised at runtime
    ChatNVIDIA = None


class NIMAdapter(LLMAdapter):
    """ChatNVIDIA-backed adapter for the NIM free tier.

    Template kwargs per model come from :data:`NIM_TEMPLATE_KWARGS`
    (verified against build.nvidia.com model pages) and are sent via
    ``extra_body`` -> NIM ``chat_template_kwargs``.
    """

    def __init__(
        self,
        model_id: str,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        pacer: Pacer | None = None,
        temperature: float = 0.2,
    ) -> None:
        super().__init__(pacer)
        if ChatNVIDIA is None:
            raise RuntimeError("install the nim extra: uv sync --extra nim")
        self.model_id = model_id
        template_kwargs = NIM_TEMPLATE_KWARGS.get(model_id, {})
        self._llm = ChatNVIDIA(
            model=model_id,
            api_key=api_key or os.getenv("NIM_API_KEY"),
            base_url=base_url or os.getenv("NIM_BASE_URL"),
            temperature=temperature,
            chat_template_kwargs=template_kwargs,
        )

    def stream(self, messages: list[dict[str, str]], **kwargs) -> Iterator[str]:
        self._pacer.acquire()
        try:
            for chunk in self._llm.stream(messages, **kwargs):
                if chunk.content:
                    yield chunk.content
            self._pacer.report_success()
        except Exception as exc:  # noqa: BLE001 - provider errors are opaque
            self._note_failure(exc)
            raise

    async def astream(self, messages: list[dict[str, str]], **kwargs) -> AsyncIterator[str]:
        self._pacer.acquire()
        try:
            async for chunk in self._llm.astream(messages, **kwargs):
                if chunk.content:
                    yield chunk.content
            self._pacer.report_success()
        except Exception as exc:  # noqa: BLE001
            self._note_failure(exc)
            raise

    def complete(self, messages: list[dict[str, str]], **kwargs) -> LLMResponse:
        self._pacer.acquire()
        try:
            result = self._llm.invoke(messages, **kwargs)
            usage = (result.response_metadata or {}).get("token_usage", {})
            self._pacer.report_success()
            return LLMResponse(
                text=str(result.content),
                finish_reason=str(result.response_metadata.get("finish_reason", "")),
                tokens_in=int(usage.get("prompt_tokens", 0)),
                tokens_out=int(usage.get("completion_tokens", 0)),
                model_id=self.model_id,
            )
        except Exception as exc:  # noqa: BLE001
            self._note_failure(exc)
            raise

    def health(self) -> bool:
        try:
            return self._llm.get_num_tokens("ping") is not None
        except Exception:  # noqa: BLE001
            return False

    def _note_failure(self, exc: Exception) -> None:
        status = getattr(exc, "status_code", None)
        # 429 = rate limit; 503 = transient server overload (observed at
        # conc=8 in the D21 probe). Both slow down and retry via pacing.
        if status == 429 or "429" in str(exc) or status == 503:
            self._pacer.report_rate_limited()
