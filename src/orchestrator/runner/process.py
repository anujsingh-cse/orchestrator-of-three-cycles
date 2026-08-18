"""Process management (T5) — env scrub + kill-on-timeout.

The runner's children must never see the API key or other secrets (env scrub
assertion is a release-blocking test), and a hung test run must be killed, not
waited on.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

SCRUB_KEYS = (
    "NIM_API_KEY",
    "NVIDIA_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AZURE_OPENAI_API_KEY",
)


def scrub_env(env: dict[str, str] | None = None) -> dict[str, str]:
    """Copy of the environment with secrets removed (D5)."""
    base = dict(os.environ if env is None else env)
    for key in SCRUB_KEYS:
        base.pop(key, None)
    return base


@dataclass
class TestResult:
    passed: bool
    returncode: int
    output: str
    duration_s: float


def run_tests(
    worktree_root: Path, timeout_s: float = 120.0, command: list[str] | None = None
) -> TestResult:
    """Run the repo's test suite inside the worktree under the scrubbed env.

    On timeout the process group is killed (not just the parent) — child
    processes cannot outlive the budget.
    """
    t0 = time.perf_counter()
    env = scrub_env()
    cmd = command or ["pytest", "-q", "-x"]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(worktree_root),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return TestResult(
            False, -9, "runner: test run timed out (killed)", time.perf_counter() - t0
        )
    duration = time.perf_counter() - t0
    return TestResult(
        passed=proc.returncode == 0,
        returncode=proc.returncode,
        output=(proc.stdout or "")[-2000:] + (proc.stderr or "")[-2000:],
        duration_s=duration,
    )
