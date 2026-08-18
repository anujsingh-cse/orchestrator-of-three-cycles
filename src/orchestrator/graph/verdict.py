"""Verdict enum and routing table (T3) — the falsification loop's decision core."""

from __future__ import annotations

from enum import StrEnum


class Verdict(StrEnum):
    """Arbiter outcome; also the routing key (verdict routing table tests)."""

    PASS = "pass"
    PATCH_FIX = "patch_fix"  # patch is close; coder retries with critique context
    RUBRIC_FAIL = "rubric_fail"  # violates a rubric rule; coder replans
    REPLAN = "replan"  # approach is wrong; coder replans
    MINOR_FIX = "minor_fix"  # cosmetic; coder quick-fixes
    ESCALATE = "escalate"  # touches a guard-railed path; human gate required
    BUDGET_EXHAUSTED = "budget_exhausted"  # loop budget spent without a pass


# D5: guard-railed paths — patches touching these go to ESCALATE, never apply.
ESCALATION_PATTERN = (
    r"^(?:secrets/|\.github/)"  # top-level secret/CI dirs
    r"|(?:^|/)(?:[^/]*lock\.json|[^/]*\.lock|\.env$)"  # lockfiles + .env anywhere
)

# T3: loop budgets (design doc: coder <= 3, adversary <= 2).
MAX_CODER_ROUNDS = 3
MAX_ADVERSARY_ROUNDS = 2


def touched_paths(patch_text: str) -> list[str]:
    """Parse ``diff --git a/<path> b/<path>`` headers out of a unified diff."""
    paths: list[str] = []
    for line in patch_text.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                path = parts[3][2:]
                if path not in paths:
                    paths.append(path)
    return paths


def escalation_hit(patch_text: str) -> list[str]:
    """Guard-railed paths touched by this patch (D5) — empty means safe."""
    import re

    return [p for p in touched_paths(patch_text) if re.search(ESCALATION_PATTERN, p)]
