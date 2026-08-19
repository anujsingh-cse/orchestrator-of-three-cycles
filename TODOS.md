# TODOS

Resolved decisions D1–D23 and implementation tasks T1–T13 are in the approved
design doc (canonical copy under `~/.gstack/projects/`). This file tracks only
open probes and blocked work.

## Open probes

- [x] **D21 — NIM rate-limit probe**: DONE 2026-08-18. Ceilings measured per
  model (see `roster.NIM_MEASURED_RPM`): coder nemotron-3-super-120b-a12b
  ~60 rpm (no 429 at conc=8); critic nemotron-3-ultra-550b-a55b ~20-25 rpm
  with a transient 503 at conc=8 (now paced); adversary glm-5.2 ~10.6 rpm
  (second run quota-drained). DeepSeek endpoints all dead (410/hang) —
  coder reassigned, see `roster.py`.
- [ ] **D22 — Ollama draft-model probe**: pull a candidate 8B (qwen3:8b
  unconfirmed), measure draft quality on 10 adversarial prompts vs NIM
  critic; decide `OLLAMA_DRAFT_MODEL`. Output: `roster.py` "draft" entry.

## Blocked on user

- [x] Explicit go for `uv sync --extra openhands` + spike execution on the
  OpenHands SDK — DONE 2026-08-19. All 5 criteria PASS (C1, C4, C5 directly;
  C2, C3 observed in SDK surface).
- [x] NIM_API_KEY availability for the D21 probe and the integration-gated
  CI job (`pytest -m integration`) — DONE 2026-08-19. Integration test passes.

## Completed (Week 1-2)

- [x] **T1** Substrate sprint (D12 scorecard) — LangGraph accepted 5/5
- [x] **T2** LLM adapter contract — NIM/Ollama/Fake + pacing (D6)
- [x] **T3** Graph — Coder→Adversary→Critic→Arbiter + Verdict routing + HITL gate (D11)
- [x] **T4** Audit sink — fail-closed SQLite DAG (D2, D7)
- [x] **T5** Runner isolation — worktree + env scrub + timeout kill (D5)
- [x] **T6** Diff apply contract — LF, git apply --check, patch_fix routing (D8)
- [x] **T12** CLI entry point — `orchestrator` command with --tui/--plain/--nim/--auto-approve

## Next (Week 3)

- [x] **T7** Tree-sitter chunker → Qdrant embedded (enrichment headers, FQN IDs, incremental)
- [x] **T8** Hybrid retrieval — sparse vectors + RRF fusion (D4)
- [x] **T9** Failure zoo — DAG view, provenance, distillation (D9, D19)
- [x] **T10** Control arm + corpus — flat loop, seed corpus methodology (D17, D18)
- [x] **T11** Ablation runner — bounded parallel lanes (D16) + report stats (D23)
- [x] **T12** TUI status pane — textual + streaming fallback (D20)

## Next (Week 4)

- [x] **T13** CI — lint, unit tests, integration test with fixture repo against NIM, prompt smoke evals (D13)

## Done (no action)

- D23 stats framing is fully specified in the design doc (held-out suites, n,
  effect size, variance, bug-type distribution) — no TODO required.

(End of file - total 58 lines)