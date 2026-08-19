"""Prompt smoke evals (D13) — 5 fixed cases per agent prompt.

Runs in CI on any prompt edit. Full ablations are deliberate research steps, not CI gates.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure src is on path for imports when run from repo root
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from orchestrator.adapters.llm.fake import FakeAdapter
from orchestrator.graph.prompts import (
    adversary_prompt,
    arbiter_prompt,
    coder_prompt,
    critic_prompt,
)

# 5 fixed test cases per agent (D13)
CODER_CASES = [
    {
        "task": "Fix off-by-one in loop",
        "attack": "",
        "critique": "",
        "round_no": 1,
    },
    {
        "task": "Add null check before dereference",
        "attack": "",
        "critique": "",
        "round_no": 1,
    },
    {
        "task": "Fix race condition in cache",
        "attack": "",
        "critique": "",
        "round_no": 1,
    },
    {
        "task": "Handle empty list edge case",
        "attack": "IndexError on empty list",
        "critique": "Missed empty list",
        "round_no": 2,
    },
    {
        "task": "Validate input before SQL query",
        "attack": "SQL injection possible",
        "critique": "Input not sanitized",
        "round_no": 2,
    },
]

ADVERSARY_CASES = [
    {
        "patch": (
            "diff --git a/foo.py b/foo.py\n"
            "- for i in range(len(items)):\n"
            "+ for i in range(len(items) - 1):"
        ),
    },
    {
        "patch": (
            "diff --git a/bar.py b/bar.py\n"
            "+ if x is not None:\n"
            "+     x.foo()"
        ),
    },
    {
        "patch": "diff --git a/baz.py b/baz.py\n+ cache[key] = value",
    },
    {
        "patch": "diff --git a/qux.py b/qux.py\n+ result = items[0]",
    },
    {
        "patch": (
            "diff --git a/corge.py b/corge.py\n"
            "+ cursor.execute(f'SELECT * FROM t WHERE x = {input}')"
        ),
    },
]

CRITIC_CASES = [
    {
        "task": "Fix off-by-one",
        "patch": "for i in range(len(items) - 1):",
        "attack": "Empty list causes IndexError",
    },
    {
        "task": "Null check",
        "patch": "if x is not None: x.foo()",
        "attack": "x could be mutated between check and call",
    },
    {
        "task": "Race condition",
        "patch": "cache[key] = value",
        "attack": "Concurrent writes lose updates",
    },
    {
        "task": "Empty list",
        "patch": "result = items[0]",
        "attack": "IndexError when items is empty",
    },
    {
        "task": "SQL injection",
        "patch": "cursor.execute(f'SELECT * FROM t WHERE x = {input}')",
        "attack": "Direct string interpolation",
    },
]

ARBITER_CASES = [
    {
        "task": "Fix off-by-one",
        "patch": "for i in range(len(items) - 1):",
        "attack": "Empty list",
        "critique": "Missed empty list",
        "coder_rounds": 1,
    },
    {
        "task": "Null check",
        "patch": "if x is not None: x.foo()",
        "attack": "TOCTOU",
        "critique": "Check-then-act race",
        "coder_rounds": 1,
    },
    {
        "task": "Cache race",
        "patch": "cache[key] = value",
        "attack": "Lost updates",
        "critique": "No locking",
        "coder_rounds": 1,
    },
    {
        "task": "Empty list",
        "patch": "if items: result = items[0]",
        "attack": "",
        "critique": "Handled",
        "coder_rounds": 2,
    },
    {
        "task": "SQL safe",
        "patch": "cursor.execute('SELECT * FROM t WHERE x = ?', (input,))",
        "attack": "",
        "critique": "Parameterized",
        "coder_rounds": 2,
    },
]


def make_fake_adapter(responses: list[str]) -> FakeAdapter:
    """Create a FakeAdapter that returns responses in sequence."""
    call_count = [0]

    def script(messages):
        idx = call_count[0]
        call_count[0] += 1
        return responses[min(idx, len(responses) - 1)]

    return FakeAdapter(model_id="fake-eval", script=script)


def eval_coder() -> tuple[bool, list[str]]:
    """Test coder prompt produces expected structure."""
    diff = (
        "diff --git a/foo.py b/foo.py\n"
        "- for i in range(len(items)):\n"
        "+ for i in range(len(items) - 1):"
    )
    adapter = make_fake_adapter([diff] * 5)

    failures = []
    for i, case in enumerate(CODER_CASES):
        messages = coder_prompt(case["task"], case["attack"], case["critique"], case["round_no"])
        response = adapter.complete(messages)

        # Check response is a unified diff
        text = response.text.strip()
        if not text.startswith("diff --git"):
            failures.append(f"Coder case {i+1}: expected unified diff, got: {text[:50]}")

    return len(failures) == 0, failures


def eval_adversary() -> tuple[bool, list[str]]:
    """Test adversary prompt produces attack output."""
    adapter = make_fake_adapter([
        "Attack: empty list causes IndexError at line 5",
    ] * 5)

    failures = []
    for i, case in enumerate(ADVERSARY_CASES):
        messages = adversary_prompt(case["patch"])
        response = adapter.complete(messages)

        text = response.text.strip()
        if not text:
            failures.append(f"Adversary case {i+1}: empty response")
        if len(text) < 10:
            failures.append(f"Adversary case {i+1}: response too short: {text}")

    return len(failures) == 0, failures


def eval_critic() -> tuple[bool, list[str]]:
    """Test critic prompt produces rubric verdict."""
    adapter = make_fake_adapter([
        "Verdict: rubric_fail. Missing empty list handling.",
    ] * 5)

    failures = []
    for i, case in enumerate(CRITIC_CASES):
        messages = critic_prompt(case["task"], case["patch"], case["attack"])
        response = adapter.complete(messages)

        text = response.text.strip()
        if not text:
            failures.append(f"Critic case {i+1}: empty response")
        if "verdict" not in text.lower() and "rubric" not in text.lower():
            failures.append(f"Critic case {i+1}: missing verdict/rubric keyword: {text[:50]}")

    return len(failures) == 0, failures


def eval_arbiter() -> tuple[bool, list[str]]:
    """Test arbiter prompt produces valid verdict enum."""
    adapter = make_fake_adapter([
        "verdict: patch_fix\nJustification: empty list not handled",
    ] * 5)

    failures = []
    valid_verdicts = {
        "pass",
        "patch_fix",
        "rubric_fail",
        "replan",
        "escalate",
        "minor_fix",
        "budget_exhausted",
    }

    for i, case in enumerate(ARBITER_CASES):
        messages = arbiter_prompt(
            case["task"],
            case["patch"],
            case["attack"],
            case["critique"],
            case["coder_rounds"],
        )
        response = adapter.complete(messages)

        text = response.text.strip()
        if not text:
            failures.append(f"Arbiter case {i+1}: empty response")
            continue

        # Check for verdict line
        import re
        match = re.search(r"verdict:\s*(\S+)", text)
        if not match:
            failures.append(f"Arbiter case {i+1}: no 'verdict:' line found: {text[:50]}")
            continue

        verdict = match.group(1).strip().rstrip(".")
        if verdict not in valid_verdicts:
            failures.append(f"Arbiter case {i+1}: invalid verdict '{verdict}': {text[:50]}")

    return len(failures) == 0, failures


def main() -> int:
    print("=== D13 Prompt Smoke Evals ===\n")

    all_failures: list[str] = []

    for name, fn in [
        ("Coder", eval_coder),
        ("Adversary", eval_adversary),
        ("Critic", eval_critic),
        ("Arbiter", eval_arbiter),
    ]:
        print(f"Running {name} smoke eval (5 cases)...")
        ok, failures = fn()
        if ok:
            print(f"  PASS: {name}")
        else:
            print(f"  FAIL: {name}")
            for f in failures:
                print(f"    - {f}")
            all_failures.extend([f"{name}: {f}" for f in failures])

    print()
    if all_failures:
        print("=== FAILURES ===")
        for f in all_failures:
            print(f"  {f}")
        return 1

    print("=== ALL SMOKE TESTS PASS ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
