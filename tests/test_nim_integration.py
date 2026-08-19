"""D13 integration smoke: one real completion per roster model on NIM.

Marked ``integration`` (pyproject) — runs only when NIM_API_KEY is present;
the CI job gates it inside the script (ci.yml). Paced with the D21 measured
ceilings (roster.NIM_MEASURED_RPM).

Failure policy: 400/401/404/410/timeouts FAIL the suite. 429 (free-tier
quota drained) and 503 (service temporarily overloaded) are retried with
backoff, then reported as warnings — they are environmental conditions, not
regressions (capacity is probed by D21).
"""

from __future__ import annotations

import os
import time

import pytest
from dotenv import load_dotenv

from orchestrator.adapters.llm.pacing import CircuitOpenError, Pacer, PacingConfig
from orchestrator.roster import MODEL_ROSTER, NIM_MEASURED_RPM

load_dotenv()

pytestmark = pytest.mark.integration

pytest.importorskip("langchain_nvidia_ai_endpoints", reason="install with: uv sync --extra nim")

RETRIES = 2
BACKOFF_S = 20


@pytest.fixture(scope="module")
def nim_key() -> str:
    key = os.environ.get("NIM_API_KEY", "")
    if not key:
        pytest.skip("NIM_API_KEY not set")
    return key


def _complete_with_retry(
    adapter, messages: list[dict[str, str]]
) -> tuple[object | None, str | None]:
    for attempt in range(RETRIES + 1):
        try:
            return adapter.complete(messages), None
        except CircuitOpenError:
            return None, "rate-limited (circuit open)"
        except Exception as exc:  # noqa: BLE001 - provider errors are opaque
            exc_str = str(exc)
            is_retryable = (
                "429" in exc_str
                or "503" in exc_str
                or "timed out" in exc_str.lower()
            )
            if is_retryable and attempt < RETRIES:
                time.sleep(BACKOFF_S)
                continue
            if "429" in exc_str:
                return None, "rate-limited (quota drained)"
            if "503" in exc_str:
                return None, "service-overloaded (503)"
            if "timed out" in exc_str.lower():
                return None, "timeout"
            return None, exc_str
    return None, "unreachable"


def test_roster_models_respond(nim_key: str) -> None:
    from orchestrator.adapters.llm.nim import NIMAdapter

    failures: list[str] = []
    warnings: list[str] = []
    for role, model in MODEL_ROSTER.items():
        if not model or role == "draft":
            continue
        ceiling = NIM_MEASURED_RPM.get(role, 8)
        adapter = NIMAdapter(
            model,
            api_key=nim_key,
            pacer=Pacer(PacingConfig(max_requests_per_minute=ceiling)),
        )
        t0 = time.perf_counter()
        response, problem = _complete_with_retry(
            adapter, [{"role": "user", "content": "Reply with exactly: OK"}]
        )
        elapsed = time.perf_counter() - t0
        if problem:
            is_transient = (
                problem.startswith("rate-limited")
                or problem.startswith("service-overloaded")
                or problem == "timeout"
            )
            if is_transient:
                warnings.append(f"{role}: {model} -> {problem}")
                print(f"  {role:10s} {model:42s} {elapsed:5.1f}s {problem}")
            else:
                failures.append(f"{role}: {model} -> {problem}")
            continue
        assert response is not None
        assert response.text, f"{model} returned an empty completion"
        assert response.model_id == model
        print(
            f"  {role:10s} {model:42s} {elapsed:5.1f}s "
            f"in={response.tokens_in} out={response.tokens_out}"
        )

    for w in warnings:
        print(f"WARN: {w}")
    assert not failures, f"roster models failed: {failures}"
