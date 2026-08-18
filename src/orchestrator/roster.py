"""Verified model roster — live-checked against the NIM API 2026-08-18.

Corrections (recorded in the design doc):
1. 2026-08-18: deepseek-v4-pro is NOT on the free tier (paid/partner only).
2. 2026-08-18: all DeepSeek API variants are dead — `deepseek-ai/deepseek-v4-flash`
   and `-pro` return 410 Gone; `deepseek-ai/deepseek-v4-flash-0731` (present in
   the /v1/models catalog) hangs indefinitely even streaming. Coder therefore
   uses the verified-fast nemotron-3-super-120b-a12b (0.4s p50).

Draft model is chosen by the probe in TODOS.md (D22) — `None` until measured.
"""

from __future__ import annotations

MODEL_ROSTER: dict[str, str | None] = {
    "coder": "nvidia/nemotron-3-super-120b-a12b",
    "adversary": "z-ai/glm-5.2",
    "critic": "nvidia/nemotron-3-ultra-550b-a55b",
    "strategist": "nvidia/nemotron-3-ultra-550b-a55b",
    "draft": None,  # Ollama 8B, chosen by D22 probe
}

# Per-model template kwargs, verified against build.nvidia.com model pages.
# Passed through ChatNVIDIA extra_body -> NIM chat_template_kwargs.
NIM_TEMPLATE_KWARGS: dict[str, dict[str, object]] = {
    "nvidia/nemotron-3-super-120b-a12b": {"enable_thinking": False},  # coder: fast
    "nvidia/nemotron-3-ultra-550b-a55b": {"enable_thinking": True},  # full-thinking critic
    "z-ai/glm-5.2": {},  # default template
}

# FastEmbed, local (no API). Enrichment headers per D3; swap flag-gated.
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384

# D21 probe (2026-08-18): measured free-tier ceilings at 80% safety margin.
# glm-5.2 ceiling estimated from the pre-quota run (10.6 rpm observed before
# the first 429); the second run was quota-drained and returned 429s.
NIM_MEASURED_RPM: dict[str, int] = {
    "coder": 47,  # nemotron-3-super-120b-a12b: no 429 at conc=8, 59.5 rpm observed
    "adversary": 8,  # glm-5.2: 10.6 rpm observed before first 429
    "critic": 16,  # nemotron-3-ultra-550b-a55b: 20-25.5 rpm observed, 503 at conc=8
}
