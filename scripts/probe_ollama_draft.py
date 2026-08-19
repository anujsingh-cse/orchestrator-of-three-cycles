"""D22 probe: measure Ollama 8B draft-model quality vs NIM critic.

Pulls a candidate 8B model (qwen3:8b by default), runs 10 adversarial prompts
through both the Ollama draft model and the NIM critic, and compares quality
to decide if it's fit for the fallback/draft role.

    uv run python scripts/probe_ollama_draft.py
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass

from dotenv import load_dotenv

from orchestrator.adapters.llm.nim import NIMAdapter
from orchestrator.adapters.llm.pacing import Pacer, PacingConfig
from orchestrator.roster import MODEL_ROSTER, NIM_MEASURED_RPM

try:
    from ollama import Client
except ImportError:
    Client = None

load_dotenv()

DEFAULT_DRAFT_MODEL = "qwen3:8b"
OLLAMA_HOST = "http://localhost:11434"

ADVERSARIAL_PROMPTS = [
    "Find the bug in this patch: it uses a default mutable argument for a list.",
    "Find the security hole: the patch passes user input directly to eval().",
    "Find the edge case: the patch assumes the list is non-empty but it could be empty.",
    "Find the race condition: the patch reads a file then writes it without locking.",
    "Find the type error: the patch treats a string as if it were an integer.",
    "Find the resource leak: the patch opens a file but never closes it.",
    "Find the off-by-one: the patch uses <= where it should use < in a loop bound.",
    "Find the injection flaw: the patch builds SQL by concatenating user strings.",
    "Find the logic error: the patch returns True when the condition is False.",
    "Find the contract violation: the patch changes a function's return type silently.",
]


@dataclass
class PromptResult:
    prompt: str
    ollama_response: str
    ollama_latency_ms: float
    ollama_error: str | None
    nim_response: str
    nim_latency_ms: float
    nim_error: str | None


def check_ollama_health() -> bool:
    if Client is None:
        return False
    try:
        client = Client(host=OLLAMA_HOST)
        client.list()
        return True
    except Exception:
        return False


def pull_model(model_id: str) -> bool:
    if Client is None:
        return False
    try:
        client = Client(host=OLLAMA_HOST)
        print(f"Pulling {model_id}...")
        for progress in client.pull(model_id, stream=True):
            if "status" in progress:
                print(f"  {progress['status']}", end="\r")
        print(f"Pulled {model_id} successfully.")
        return True
    except Exception as exc:
        print(f"Failed to pull {model_id}: {exc}")
        return False


def run_ollama(model_id: str, prompt: str) -> tuple[str, float, str | None]:
    if Client is None:
        return "", 0.0, "ollama package not installed"
    client = Client(host=OLLAMA_HOST)
    start = time.perf_counter()
    try:
        messages = [{"role": "user", "content": prompt}]
        result = client.chat(model=model_id, messages=messages, stream=False)
        latency = (time.perf_counter() - start) * 1000
        return result.message.content, latency, None
    except Exception as exc:
        latency = (time.perf_counter() - start) * 1000
        return "", latency, str(exc)


def run_nim_critic(prompt: str) -> tuple[str, float, str | None]:
    if not os.environ.get("NIM_API_KEY"):
        return "", 0.0, "NIM_API_KEY not set"
    critic_model = MODEL_ROSTER.get("critic")
    if not critic_model:
        return "", 0.0, "critic model not in roster"
    ceiling = NIM_MEASURED_RPM.get("critic", 16)
    adapter = NIMAdapter(
        critic_model,
        api_key=os.environ["NIM_API_KEY"],
        pacer=Pacer(PacingConfig(max_requests_per_minute=ceiling)),
    )
    start = time.perf_counter()
    try:
        response = adapter.complete([{"role": "user", "content": prompt}])
        latency = (time.perf_counter() - start) * 1000
        return response.text, latency, None
    except Exception as exc:
        latency = (time.perf_counter() - start) * 1000
        return "", latency, str(exc)


def evaluate_quality(ollama_resp: str, nim_resp: str) -> dict[str, float]:
    """Simple heuristic quality metrics."""
    ollama_len = len(ollama_resp.split())
    nim_len = len(nim_resp.split())
    return {
        "ollama_word_count": ollama_len,
        "nim_word_count": nim_len,
        "length_ratio": ollama_len / max(nim_len, 1),
        "ollama_nonempty": 1.0 if ollama_resp.strip() else 0.0,
        "nim_nonempty": 1.0 if nim_resp.strip() else 0.0,
    }


def main() -> int:
    print("=== D22 Ollama Draft Model Probe ===\n")

    if not check_ollama_health():
        print("ERROR: Ollama not running at http://localhost:11434")
        print("Start Ollama service first: `ollama serve`")
        return 2

    if not os.environ.get("NIM_API_KEY"):
        print("ERROR: NIM_API_KEY not set in .env")
        return 2

    model_id = DEFAULT_DRAFT_MODEL
    if not pull_model(model_id):
        return 3

    print(f"\nRunning {len(ADVERSARIAL_PROMPTS)} adversarial prompts...\n")

    results: list[PromptResult] = []
    quality_scores: list[dict] = []

    for i, prompt in enumerate(ADVERSARIAL_PROMPTS, 1):
        print(f"[{i}/{len(ADVERSARIAL_PROMPTS)}] {prompt[:60]}...")

        ollama_resp, ollama_lat, ollama_err = run_ollama(model_id, prompt)
        nim_resp, nim_lat, nim_err = run_nim_critic(prompt)

        quality = evaluate_quality(ollama_resp, nim_resp)

        result = PromptResult(
            prompt=prompt,
            ollama_response=ollama_resp,
            ollama_latency_ms=round(ollama_lat, 1),
            ollama_error=ollama_err,
            nim_response=nim_resp,
            nim_latency_ms=round(nim_lat, 1),
            nim_error=nim_err,
        )
        results.append(result)
        quality_scores.append(quality)

        ollama_status = "OK" if not ollama_err else f"ERR: {ollama_err[:40]}"
        nim_status = "OK" if not nim_err else f"ERR: {nim_err[:40]}"
        print(f"  Ollama: {ollama_lat:.0f}ms {ollama_status}")
        print(f"  NIM:    {nim_lat:.0f}ms {nim_status}")
        print(f"  Quality: length_ratio={quality['length_ratio']:.2f}")

    ollama_ok = sum(1 for r in results if not r.ollama_error)
    nim_ok = sum(1 for r in results if not r.nim_error)
    avg_ratio = sum(q["length_ratio"] for q in quality_scores) / len(quality_scores)
    ollama_nonempty = sum(q["ollama_nonempty"] for q in quality_scores) / len(quality_scores)
    nim_nonempty = sum(q["nim_nonempty"] for q in quality_scores) / len(quality_scores)

    print("\n=== SUMMARY ===")
    print(f"Model tested: {model_id}")
    print(f"Ollama success: {ollama_ok}/{len(results)}")
    print(f"NIM critic success: {nim_ok}/{len(results)}")
    print(f"Avg length ratio (Ollama/NIM): {avg_ratio:.2f}")
    print(f"Ollama non-empty rate: {ollama_nonempty:.0%}")
    print(f"NIM non-empty rate: {nim_nonempty:.0%}")

    nonempty_status = "PASS" if ollama_nonempty >= 0.9 else "FAIL"
    ratio_status = "PASS" if avg_ratio >= 0.3 else "FAIL"
    decision = "PASS" if (ollama_nonempty >= 0.9 and avg_ratio >= 0.3) else "FAIL"
    print(f"\nDecision: {decision}")
    print(f"  - Non-empty threshold (>=90%): {nonempty_status} ({ollama_nonempty:.0%})")
    print(f"  - Length ratio threshold (>=0.3): {ratio_status} ({avg_ratio:.2f})")

    output = {
        "model_id": model_id,
        "decision": decision,
        "summary": {
            "ollama_success_rate": ollama_ok / len(results),
            "nim_success_rate": nim_ok / len(results),
            "avg_length_ratio": avg_ratio,
            "ollama_nonempty_rate": ollama_nonempty,
            "nim_nonempty_rate": nim_nonempty,
        },
        "results": [asdict(r) for r in results],
    }

    with open("d22_probe_results.json", "w") as f:
        json.dump(output, f, indent=2)
    print("\nResults written to d22_probe_results.json")

    if decision == "PASS":
        print(f"\n>>> Recommendation: Set roster.py draft = \"{model_id}\"")
    else:
        print("\n>>> Recommendation: Try a different 8B model or defer draft fallback")

    return 0 if decision == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
