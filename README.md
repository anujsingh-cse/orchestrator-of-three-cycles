# Orchestrator of Three Cycles

Research harness: does an **adversarial falsification loop** (Coder proposes →
Adversary attacks → Critic arbitrates → patches fix the attack) produce
measurably more robust patches than a flat single-shot loop, at zero cost on
the NVIDIA NIM free tier?

Status: **Week 1 — substrate sprint + scaffold in progress** (design doc:
`docs/decisions/0001-substrate-scorecard.md` + approved plan).

## Premises (revised, office-hours 2026-08-18)

- P1: research harness; the deliverable is *evidence* (controlled ablation,
  held-out suites, effect sizes — not a product).
- P2: minimal safe-execution tier day one: git worktrees + process isolation +
  allowlists. Docker deferred.
- P3: adversarial falsification is the sole headline; RAPTOR/KG/tri-fusion are
  flag-gated experimental extras.
- P3a: OpenHands V1 SDK is evaluated as an alternative substrate in the week-1
  sprint (scorecard: `docs/decisions/0001-substrate-scorecard.md`).
- P3b: the failure zoo (falsified patches + attacks, with provenance) is a
  headline deliverable.
- P4: NIM zero-cost is a community feature; Ollama is a one-line drop-in.

## Architecture (week-1 seam view)

```
                  ┌─ audit DAG (SQLite, D2/D7) ── sole authority ──┐
                  │                                                ▼
[TUI] → [Graph: Coder→Adversary→Critic→Arbiter] → [AuditSink] → [Zoo view (D9)]
          │
          └── [LLMAdapter (D6)] ─ NIM (zero-cost) ─ Ollama (drop-in)
              pacing: leaky bucket + jittered backoff + circuit breaker
```

## Setup

```powershell
uv sync --all-extras            # or: uv sync --extra nim
Copy-Item .env.example .env     # then paste your NIM_API_KEY
uv run pytest -q                # unit suite (mocked LLM, no key needed)
```

## Substrate spike (week 1, D12)

```powershell
uv run python scripts/spike_langgraph.py    # runnable now, mocked LLM
uv run python scripts/spike_openhands.py    # needs uv sync --extra openhands
```

The 5-criteria scorecard and current verdict live in
`docs/decisions/0001-substrate-scorecard.md`.

## Layout

```
src/orchestrator/
  adapters/llm/   LLM seam: pacing (D6), NIM, Ollama drop-in
  audit/          AuditEvent schema + fail-closed SQLite sink (D2/D7)
scripts/          substrate spikes, run helpers
tests/            unit + release-blocking tests (D11)
docs/decisions/   scorecard + ADRs
```

## Deliverable contract (D19)

The distributed corpus is the failure zoo, with provenance and license
policy per `NOTICE`. Release-blocking tests are listed in
`docs/decisions/0001-substrate-scorecard.md` and `TODOS.md`.