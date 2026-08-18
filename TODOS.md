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

- [ ] Explicit go for `uv sync --extra openhands` + spike execution on the
  OpenHands SDK (deferred by design — you control the install moment).
- [ ] NIM_API_KEY availability for the D21 probe and the integration-gated
  CI job (`pytest -m integration`).

## Done (no action)

- D23 stats framing is fully specified in the design doc (held-out suites, n,
  effect size, variance, bug-type distribution) — no TODO required.