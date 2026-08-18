# ADR 0001 — Substrate scorecard (D12)

Status: **PROPOSED** (verdict lands here when the week-1 spike completes)
Date: 2026-08-18

## Context

Two candidate substrates for the week-1 sprint:

- **LangGraph 1.x** (SQLite checkpointer) — baseline, adopted by default.
- **OpenHands V1 SDK** (MIT, arXiv 2511.03690, MLSys 2026) — challenger.

Decision rule: any criterion FAIL on a candidate defaults to LangGraph. No
scorecard can be left half-filled at the end of the sprint — every criterion
gets PASS / FAIL / n/a-with-reason.

## The five criteria (D12)

| # | Criterion | How to run | LangGraph spike | OpenHands spike |
|---|-----------|------------|-----------------|-----------------|
| 1 | Event-stream replay equivalent to DAG replay | Replay a session from the checkpoint/event log; final state must equal the original run (deterministic fakes) | `scripts/spike_langgraph.py` | `scripts/spike_openhands.py` |
| 2 | Interrupt/resume inside the SDK loop | Hit `interrupt()` in a gate node, `Command(resume=...)`; side effects must NOT re-execute | `scripts/spike_langgraph.py` | week-2 graph build (T4) |
| 3 | NIM multi-model routing pluggable | Two distinct models bound to two nodes; each called exactly once | `scripts/spike_langgraph.py` | adapter-layer (spike #1) |
| 4 | LocalWorkspace single-node Windows | Round-trip file write through the workspace API on this machine | n/a by design | `scripts/spike_openhands.py` |
| 5 | No hidden Docker dependency | Execution path must reference no container runtime | `scripts/spike_langgraph.py` | `scripts/spike_openhands.py` |

## Results (filled at end of week 1)

| # | LangGraph | OpenHands | Notes |
|---|-----------|-----------|-------|
| 1 | **PASS** | _pending_ | fresh-thread replay matched final state (spike run 2026-08-18) |
| 2 | **PASS** | _pending_ | paused at gate; coder ran exactly once across interrupt+resume |
| 3 | **PASS** | _pending_ | two models bound to two nodes, one call each |
| 4 | **PASS** (n/a by design) | _pending_ | no workspace abstraction; process-local |
| 5 | **PASS** | _pending_ | sqlite checkpointer; zero container runtime |

## Decision (filled when verdicts are in)

- [ ] **ACCEPTED**: LangGraph remains the substrate (all 5 PASS or OpenHands
      FAILed any).
- [ ] **REJECTED / SWITCH**: OpenHands SDK becomes the substrate (all 5 PASS
      on OpenHands AND at least one criterion FAILs on LangGraph).

Superpowers the reviewer must re-check on any switch: D2 (audit DAG sole
authority — SDK EventService disabled), D6 (pacing lives in our adapter
layer regardless), D8 (diff apply contract is substrate-agnostic).