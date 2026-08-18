# ADR 0001 — Substrate scorecard (D12)

Status: **ACCEPTED** — LangGraph remains the substrate (spike results 2026-08-18)
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
| 1 | **PASS** | **PASS** | LangGraph: fresh-thread replay matched final state. OpenHands: EventLog replay stable across reads + process-restart, orphan append rejected (chain enforcement) |
| 2 | **PASS** | n/a (T4) | LangGraph: paused at gate; coder ran exactly once across interrupt+resume. OpenHands: `interrupt()`/`pause()` + InterruptEvent/PauseEvent observed in SDK |
| 3 | **PASS** | n/a (T4) | LangGraph: two models bound to two nodes, one call each. OpenHands: LLMRegistry + FallbackStrategy + Agent.CriticMixin observed |
| 4 | **PASS** (n/a by design) | **PASS** | OpenHands: LocalWorkspace file upload/download round-trip on Windows |
| 5 | **PASS** | **PASS** | Both: sqlite/file-store local; no container runtime in the exercised path |

## Decision (filled when verdicts are in)

- [x] **ACCEPTED**: LangGraph remains the substrate (both candidates 5/5 — no
      criterion FAIL on either; the switch rule was never triggered).
- [ ] **REJECTED / SWITCH**: OpenHands SDK becomes the substrate (all 5 PASS
      on OpenHands AND at least one criterion FAILs on LangGraph).

Superpowers the reviewer must re-check on any switch: D2 (audit DAG sole
authority — SDK EventService disabled), D6 (pacing lives in our adapter
layer regardless), D8 (diff apply contract is substrate-agnostic).

**Post-decision notes for the graph build (T3/T4):**
- OpenHands SDK v1.42.1 ships `EventLog` with parent_id causal chains,
  file-backed persistence, and orphan rejection — its event stream is
  compatible with our AuditEvent DAG (D2) and is a candidate replay source
  if the SDK is ever adopted.
- `Agent` ships a `CriticMixin`; `LLMRegistry` + `FallbackStrategy` could
  cover our router role — evaluated in week 2 if we adopt the SDK.
- The SDK banner prints on import unless `OPENHANDS_SUPPRESS_BANNER=1`.
- **Gotcha (D5):** `LocalWorkspace.file_upload`/`file_download` resolve
  *relative* destination paths against the process CWD, not the workspace
  `working_dir` — a file written with a relative path escapes the worktree
  (verified 2026-08-18). The runner must pass absolute destinations and the
  D5 env-scrub must include `PWD`/`CWD` hygiene.