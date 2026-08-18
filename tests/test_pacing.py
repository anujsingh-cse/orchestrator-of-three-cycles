"""D6 pacing unit tests: leaky bucket, jittered backoff, circuit breaker."""

from __future__ import annotations

import time

import pytest

from orchestrator.adapters.llm.pacing import CircuitOpenError, Pacer, PacingConfig


def test_circuit_breaker_trips_after_threshold() -> None:
    # Small-but-nonzero backoff: the breaker must hold open for a window.
    pacer = Pacer(PacingConfig(circuit_open_after_failures=3, base_backoff_seconds=0.01))
    for _ in range(3):
        pacer.report_rate_limited()
    with pytest.raises(CircuitOpenError):
        pacer.acquire()


def test_success_resets_failure_count() -> None:
    pacer = Pacer(PacingConfig(circuit_open_after_failures=3, base_backoff_seconds=0.0))
    pacer.report_rate_limited()
    pacer.report_rate_limited()
    pacer.report_success()
    pacer.report_rate_limited()
    pacer.report_rate_limited()
    pacer.acquire()  # 2 consecutive failures after reset: still under threshold


def test_leaky_bucket_limits_burst() -> None:
    cfg = PacingConfig(max_requests_per_minute=60, burst=1)  # 1 token/s
    pacer = Pacer(cfg)
    pacer.acquire()  # drains the single token
    start = time.monotonic()
    pacer.acquire()  # must wait for refill
    elapsed = time.monotonic() - start
    assert elapsed >= 0.9  # 1 token at 60 rpm = 1s/request, minus loop granularity


def test_burst_above_one_allows_immediate_second_acquire() -> None:
    pacer = Pacer(PacingConfig(max_requests_per_minute=6, burst=5))
    pacer.acquire()
    pacer.acquire()  # burst=5: second token available instantly
