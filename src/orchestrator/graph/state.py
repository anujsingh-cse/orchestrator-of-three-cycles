"""Session state (T3) — the graph's typed channel.
No competing stores (D2): everything the loop needs lives here or in the sink.
"""

from __future__ import annotations

from typing import TypedDict


class SessionState(TypedDict, total=False):
    task: str
    patch: str
    attack: str
    critique: str
    verdict: str
    gate_decision: str | None
    escalated: list[str]
    coder_rounds: int
    adversary_rounds: int
    thread_id: str
    last_event_id: str | None
    test_passed: bool | None
    trace: list[str]  # node names executed, for the TUI stream
