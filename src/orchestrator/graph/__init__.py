"""Graph package (T3) — falsification loop over the audit-wired nodes."""

from orchestrator.graph.builder import GATE_APPROVE, GATE_REJECT, build_session_graph, run_session
from orchestrator.graph.state import SessionState
from orchestrator.graph.verdict import (
    ESCALATION_PATTERN,
    MAX_ADVERSARY_ROUNDS,
    MAX_CODER_ROUNDS,
    Verdict,
    escalation_hit,
    touched_paths,
)

__all__ = [
    "ESCALATION_PATTERN",
    "GATE_APPROVE",
    "GATE_REJECT",
    "MAX_ADVERSARY_ROUNDS",
    "MAX_CODER_ROUNDS",
    "SessionState",
    "Verdict",
    "build_session_graph",
    "escalation_hit",
    "run_session",
    "touched_paths",
]
