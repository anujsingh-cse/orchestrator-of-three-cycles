# Orchestrator of Three Cycles

**The big question:** Does having AI agents argue with each other produce better code fixes than a single AI working alone?

## What This Does

Imagine three AI agents working together to fix a bug:

1. **The Coder** — proposes a fix
2. **The Adversary** — tries to break the fix (finds edge cases, security holes, logic errors)
3. **The Critic** — judges whether the fix actually works and addresses the attacks

They go back and forth until the Critic says "this is good" or "needs more work." A human gatekeeper approves the final fix before it's applied.

We run this loop against a **control group** (single AI, no debate) on real bugs from real projects. The goal: measurable evidence that the debate loop produces more robust fixes.

## What You Get

- **Evidence, not a product** — This is a research tool. The output is data: which approach works better, by how much, and on what kinds of bugs.
- **A "failure zoo"** — Every broken fix and attack is saved with full provenance. This becomes a regression corpus for future research.
- **Zero API cost** — Runs on NVIDIA's free NIM tier. Local Ollama models work as a drop-in alternative.

## Quick Start (Windows PowerShell)

```powershell
# 1. Install dependencies
uv sync --all-extras

# 2. Add your free NVIDIA NIM API key
# Get one at https://build.nvidia.com
Copy-Item .env.example .env
# Edit .env and paste your key

# 3. Run tests (no API key needed for unit tests)
uv run pytest -q
```

## How It Works (Plain English)

```
User Task → [Coder proposes fix] → [Adversary attacks] → [Critic judges] → [Human approves] → Done
                    ↑_____________|_____________↑
                         Loop repeats until Critic is satisfied
```

Every step is recorded in an audit log (SQLite) that can be replayed exactly.

## Current Status

**Week 3 of 4** — Core loop, audit system, retrieval, failure zoo, and ablation runner all working. 124 tests passing.

Remaining:
- **T13** — CI pipeline with integration tests
- **D22** — Test a local 8B model as a fast "draft" fallback

## Key Files

| Folder | Purpose |
|--------|---------|
| `src/orchestrator/adapters/llm/` | Connects to NIM (free) or Ollama (local) |
| `src/orchestrator/graph/` | The 3-agent debate loop |
| `src/orchestrator/audit/` | Tamper-proof session log |
| `src/orchestrator/retrieval/` | Finds relevant code for the agents |
| `src/orchestrator/ablation/` | Runs controlled experiments |
| `src/orchestrator/zoo/` | Builds the failure corpus |
| `scripts/` | Quick spikes and probes |
| `tests/` | 124 tests (unit + integration) |

## Why This Matters

Most AI coding tools work in one shot: you ask, they answer. But bugs often hide in edge cases. By forcing an adversarial debate, we catch those hidden flaws before the code ships — and we measure whether it actually helps.

## License

MIT — open source. The failure zoo data follows the license of each source repository (see `NOTICE`).