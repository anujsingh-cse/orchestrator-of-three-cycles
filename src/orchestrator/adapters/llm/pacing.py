"""D6: per-model pacing — leaky bucket + jittered backoff + circuit breaker.

Lives in the LLM adapter contract (P4 seam) so NIM, Ollama, and any future
provider share one pacing path. Defaults are placeholders; per-model measured
ceilings live in `roster.NIM_MEASURED_RPM` (D21 probe, 2026-08-18) and the
graph wiring (T3) builds pacers from them.
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class PacingConfig:
    max_requests_per_minute: int = 40
    burst: int = 5
    base_backoff_seconds: float = 1.0
    max_backoff_seconds: float = 60.0
    jitter: float = 0.25
    circuit_open_after_failures: int = 3


class CircuitOpenError(RuntimeError):
    """Rate-limit storm; caller should escalate (Ollama fallback or HITL)."""


class Pacer:
    """Leaky bucket + exponential backoff with jitter; 429-aware breaker.

    Thread-safe: one instance per model, shared across graph threads.
    """

    def __init__(self, config: PacingConfig | None = None) -> None:
        self._cfg = config or PacingConfig()
        self._lock = threading.Lock()
        self._bucket = float(self._cfg.burst)
        self._last_fill = time.monotonic()
        self._failures = 0
        self._circuit_open = False
        self._circuit_open_until = 0.0

    def acquire(self) -> None:
        """Block until a request slot is available; raise if circuit open."""
        while True:
            with self._lock:
                if self._circuit_open:
                    if time.monotonic() < self._circuit_open_until:
                        raise CircuitOpenError("pacing circuit is open")
                    self._circuit_open = False
                    self._failures = 0
                self._refill()
                if self._bucket >= 1.0:
                    self._bucket -= 1.0
                    return
                wait = (1.0 - self._bucket) * (60.0 / self._cfg.max_requests_per_minute)
            time.sleep(min(max(wait, 0.0), 1.0))

    def report_success(self) -> None:
        with self._lock:
            self._failures = 0

    def report_rate_limited(self) -> None:
        """Record a 429: back off; trip the breaker after N consecutive."""
        with self._lock:
            self._failures += 1
            if self._failures >= self._cfg.circuit_open_after_failures:
                self._circuit_open = True
                self._circuit_open_until = time.monotonic() + self._backoff()
                self._failures = 0
                return
        time.sleep(self._backoff())

    def _backoff(self) -> float:
        exponent = max(self._failures - 1, 0)
        base = min(self._cfg.base_backoff_seconds * (2**exponent), self._cfg.max_backoff_seconds)
        return base * (1.0 + random.uniform(-self._cfg.jitter, self._cfg.jitter))

    def _refill(self) -> None:
        now = time.monotonic()
        self._bucket = min(
            self._bucket + (now - self._last_fill) * (self._cfg.max_requests_per_minute / 60.0),
            float(self._cfg.burst),
        )
        self._last_fill = now
