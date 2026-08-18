"""Verified model roster (2026 NVIDIA NIM catalog; office-hours session).

Correction 2026-08-18: the zero-cost free tier serves the FLASH variant.
deepseek-v4-pro exists on build.nvidia.com but only as a paid/partner
serverless endpoint — NOT the free API. Coder therefore uses v4-flash
(Non-Think mode, fastest).

Draft model is chosen by the probe in TODOS.md (D22) — `None` until measured.
"""
from __future__ import annotations

MODEL_ROSTER: dict[str, str | None] = {
    "coder": "deepseek-ai/deepseek-v4-flash",
    "adversary": "z-ai/glm-5.2",
    "critic": "nvidia/nemotron-3-ultra-550b-a55b",
    "strategist": "nvidia/nemotron-3-ultra-550b-a55b",
    "draft": None,  # Ollama 8B, chosen by D22 probe
}

# Per-model template kwargs, verified against build.nvidia.com model pages.
# Passed through ChatNVIDIA extra_body -> NIM chat_template_kwargs.
NIM_TEMPLATE_KWARGS: dict[str, dict[str, object]] = {
    "deepseek-ai/deepseek-v4-flash": {"thinking": False},  # coder: Non-Think, fast
    "nvidia/nemotron-3-ultra-550b-a55b": {"enable_thinking": True},  # full-thinking critic
    "z-ai/glm-5.2": {},  # default template
}

# FastEmbed, local (no API). Enrichment headers per D3; swap flag-gated.
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384
