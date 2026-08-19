"""Agent prompts (T3) — single module so D13 prompt-smoke evals can pin them."""

from __future__ import annotations

CODER_SYSTEM = """You are the Coder in a falsification loop. You propose patches to a
research repo's codebase. You never touch secrets/, .github/, *.lock.json, or
.env files. Output ONLY a unified git diff (diff --git a/... b/... format) —
no commentary, no fenced blocks. Do NOT include "index" lines — git apply
works without them. Each file must appear at most once in the diff."""

ADVERSARY_SYSTEM = """You are the Adversary. Your job is to BREAK the proposed patch:
find the edge case, the security hole, the style-violating or contract-breaking
flaw. You are not polite — you are thorough. Output a concise list of concrete
falsifications, each with: file, line/construct, why it fails, a counterexample
if possible."""

CRITIC_SYSTEM = """You are the Critic. You judge the Coder's patch against the
project's rubric and the Adversary's falsifications. Be fair: the patch may
already be correct. Output a rubric verdict and a ranked list of concrete
defects (or "none") that the Coder must address next round."""

ARBITER_SYSTEM = """You are the Arbiter. Decide the loop outcome from the latest
patch, the Adversary's falsifications, the Critic's rubric verdict, and the
remaining loop budget. Output EXACTLY one line:
verdict: <pass|patch_fix|rubric_fail|replan|minor_fix|escalate|budget_exhausted>
plus one short justification line. pass only when the patch is genuinely good."""


def coder_prompt(task: str, attack: str, critique: str, round_no: int) -> list[dict[str, str]]:
    context = f"Task: {task}"
    if attack:
        context += f"\n\nAdversary falsifications (last round):\n{attack}"
    if critique:
        context += f"\n\nCritic rubric verdict (last round):\n{critique}"
    context += f"\n\nProduce a unified diff addressing the task (round {round_no})."
    return [{"role": "system", "content": CODER_SYSTEM}, {"role": "user", "content": context}]


def adversary_prompt(patch: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": ADVERSARY_SYSTEM},
        {"role": "user", "content": f"Break this patch:\n\n{patch}"},
    ]


def critic_prompt(task: str, patch: str, attack: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": CRITIC_SYSTEM},
        {
            "role": "user",
            "content": (
                f"Task: {task}\n\nPatch under review:\n{patch}"
                f"\n\nAdversary falsifications:\n{attack}"
            ),
        },
    ]


def arbiter_prompt(
    task: str, patch: str, attack: str, critique: str, coder_rounds: int
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": ARBITER_SYSTEM},
        {
            "role": "user",
            "content": (
                f"Task: {task}\n\nPatch (round {coder_rounds}):\n{patch}"
                f"\n\nAdversary:\n{attack}\n\nCritic:\n{critique}"
            ),
        },
    ]
