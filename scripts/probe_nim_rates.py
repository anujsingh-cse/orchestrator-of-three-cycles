"""D21 probe: measure NVIDIA NIM free-tier rate ceilings per model.

Ramps concurrency {1, 2, 4, 8} until a 429 appears (or the cap is reached),
records successes/latency/tokens, and prints a measured PacingConfig
suggestion per model. Also validates the current roster is live on NIM.

    uv run python scripts/probe_nim_rates.py
"""
from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import load_dotenv
from openai import OpenAI

from orchestrator.roster import MODEL_ROSTER, NIM_TEMPLATE_KWARGS

load_dotenv()  # NIM_API_KEY from .env (gitignored)

BASE_URL = "https://integrate.api.nvidia.com/v1"
MAX_TOKENS = 16
ROUNDS = [1, 2, 4, 8]
REQS_PER_ROUND = 2
REQUEST_TIMEOUT_S = 45.0


def fire(client: OpenAI, model: str, template_kwargs: dict[str, object]) -> tuple[str, float, int]:
    start = time.perf_counter()
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            temperature=0.2,
            max_tokens=MAX_TOKENS,
            extra_body={"chat_template_kwargs": template_kwargs},
        )
        latency = time.perf_counter() - start
        return "ok", latency, resp.usage.completion_tokens or 0
    except Exception as exc:  # noqa: BLE001 - classify by status
        status = getattr(exc, "status_code", None)
        latency = time.perf_counter() - start
        if status == 429 or "429" in str(exc):
            return "429", latency, 0
        return f"err:{status}", latency, 0


def probe_model(model: str, template_kwargs: dict[str, object]) -> dict[str, object]:
    client = OpenAI(
        base_url=BASE_URL,
        api_key=os.environ["NIM_API_KEY"],
        timeout=REQUEST_TIMEOUT_S,
        max_retries=0,
    )
    record = {"model": model, "rounds": [], "first_429_at": None}
    successes = 0
    start = time.perf_counter()
    for concurrency in ROUNDS:
        results: list[str] = []
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [
                pool.submit(fire, client, model, template_kwargs)
                for _ in range(REQS_PER_ROUND)
            ]
            for future in as_completed(futures):
                results.append(future.result())
        statuses = [r[0] for r in results]
        record["rounds"].append(
            {
                "concurrency": concurrency,
                "ok": statuses.count("ok"),
                "429": statuses.count("429"),
                "err": [s for s in statuses if s not in ("ok", "429")],
                "avg_latency_s": round(sum(r[1] for r in results) / len(results), 2),
            }
        )
        successes += statuses.count("ok")
        if statuses.count("429"):
            elapsed_min = (time.perf_counter() - start) / 60.0
            record["first_429_at"] = round(successes / elapsed_min, 1) if elapsed_min > 0 else None
            break
        time.sleep(0.5)
    elapsed_min = (time.perf_counter() - start) / 60.0
    record["total_ok"] = successes
    record["observed_rpm"] = round(successes / elapsed_min, 1) if elapsed_min > 0 else None
    return record


def main() -> int:
    if not os.environ.get("NIM_API_KEY"):
        print("NIM_API_KEY not set — copy .env.example to .env and fill it in.")
        return 2

    models = [(k, v) for k, v in MODEL_ROSTER.items() if v and k != "draft"]
    print(f"Probing {len(models)} models at {BASE_URL} ...")
    results = [probe_model(model, NIM_TEMPLATE_KWARGS.get(model, {})) for _, model in models]

    print("\n=== D21 RATE PROBE RESULTS ===")
    for r in results:
        print(f"\n  model: {r['model']}")
        for rd in r["rounds"]:
            print(
                f"    conc={rd['concurrency']:>2}  ok={rd['ok']}/{REQS_PER_ROUND}  "
                f"429={rd['429']}  avg_latency={rd['avg_latency_s']}s  err={rd['err']}"
            )
        ceiling = r["first_429_at"] or r["observed_rpm"]
        print(f"    total_ok={r['total_ok']}  observed_rpm={r['observed_rpm']}  "
              f"first_429_rpm={r['first_429_at']}")

    print("\n=== SUGGESTED PacingConfig (80% safety margin) ===")
    for r in results:
        ceiling = (r["first_429_at"] or r["observed_rpm"]) or 0
        suggested = max(int(ceiling * 0.8), 1)
        print(f"  {r['model']}: max_requests_per_minute={suggested}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
