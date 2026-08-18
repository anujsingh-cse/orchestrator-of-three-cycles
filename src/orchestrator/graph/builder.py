"""Graph builder (T3) — LangGraph StateGraph with HITL gate + routing table.

Flow (design doc diagram):
START -> coder -> adversary -> critic -> arbiter -> gate -> record_gate -> runner -> END
                ^                    ^                          |
                |____ retry verdicts (budget-checked) __________|
                (replan/patch_fix/minor_fix/rubric_fail)        approve -> runner / reject -> END

- The gate is side-effect-free (D11): only ``interrupt``; the decision is
  recorded by ``record_gate`` which runs exactly once per transition.
- The checkpointer (MemorySaver) is a TRANSIENT runtime resume mechanism; the
  durable record is the audit DAG only (D2 — no competing store).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from orchestrator.adapters.llm.base import LLMAdapter
from orchestrator.audit.sink import AuditSink
from orchestrator.graph.nodes import (
    NodeFn,
    make_adversary,
    make_arbiter,
    make_coder,
    make_critic,
    make_runner,
)
from orchestrator.graph.state import SessionState
from orchestrator.graph.verdict import MAX_ADVERSARY_ROUNDS, MAX_CODER_ROUNDS, Verdict

GATE_APPROVE = "approve"
GATE_REJECT = "reject"


def route_from_coder(state: SessionState) -> str:
    if state.get("adversary_rounds", 0) < MAX_ADVERSARY_ROUNDS:
        return "adversary"
    return "critic"


def route_from_arbiter(state: SessionState) -> str:
    verdict = state.get("verdict")
    if verdict in (Verdict.PASS.value, Verdict.ESCALATE.value):
        return "gate"
    if verdict == Verdict.BUDGET_EXHAUSTED.value:
        return END
    if verdict in (
        Verdict.PATCH_FIX.value,
        Verdict.RUBRIC_FAIL.value,
        Verdict.REPLAN.value,
        Verdict.MINOR_FIX.value,
    ):
        return "coder"
    return END  # unknown verdict -> END; malformed arbiter output already escalates


def route_from_record_gate(state: SessionState) -> str:
    if state.get("gate_decision") == GATE_APPROVE:
        return "runner"
    return END


def route_from_runner(state: SessionState) -> str:
    if state.get("test_passed"):
        return END
    if state.get("coder_rounds", 0) < MAX_CODER_ROUNDS:
        return "coder"  # tests failed -> patch_fix semantics
    return END


def make_gate() -> NodeFn:
    """Side-effect-free HITL gate (D11): interrupt only, no writes, no IO."""

    def gate(state: SessionState) -> dict[str, Any]:
        payload = {
            "prompt": "Approve applying this patch?",
            "patch": state.get("patch", ""),
            "escalated": state.get("escalated", []),
        }
        decision = interrupt(payload)
        return {
            "gate_decision": decision if decision in (GATE_APPROVE, GATE_REJECT) else GATE_REJECT
        }

    return gate


def make_record_gate(sink: AuditSink, agent_id: str) -> NodeFn:
    """Runs once per transition; records the human decision on the DAG."""

    def record_gate(state: SessionState) -> dict[str, Any]:
        from orchestrator.audit.event import AuditEvent

        event = AuditEvent(
            parent_event_id=state.get("last_event_id"),
            thread_id=state["thread_id"],
            node="gate",
            agent_id=agent_id,
            model_id="human",
            input_hash=AuditEvent.content_hash({"patch": state.get("patch", "")}),
            output_hash=AuditEvent.content_hash({"gate_decision": state.get("gate_decision")}),
            gate_decision=state.get("gate_decision"),
        )
        return {"last_event_id": sink.write_ahead(event)}

    return record_gate


def build_session_graph(
    *,
    adapters: dict[str, LLMAdapter],
    sink: AuditSink,
    worktree_root: Any,
    agent_ids: dict[str, str] | None = None,
):
    """Compile the falsification loop. ``adapters`` keys: coder, adversary, critic, arbiter."""
    ids = agent_ids or {
        role: f"{role}-agent" for role in ("coder", "adversary", "critic", "arbiter")
    }
    graph = StateGraph(SessionState)
    graph.add_node("coder", make_coder(adapters["coder"], sink, ids["coder"]))
    graph.add_node("adversary", make_adversary(adapters["adversary"], sink, ids["adversary"]))
    graph.add_node("critic", make_critic(adapters["critic"], sink, ids["critic"]))
    graph.add_node("arbiter", make_arbiter(adapters["arbiter"], sink, ids["arbiter"]))
    graph.add_node("gate", make_gate())
    graph.add_node("record_gate", make_record_gate(sink, "human-gate"))
    graph.add_node("runner", make_runner(worktree_root, sink, ids.get("runner", "runner")))

    graph.add_edge(START, "coder")
    graph.add_conditional_edges(
        "coder", route_from_coder, {"adversary": "adversary", "critic": "critic"}
    )
    graph.add_edge("adversary", "critic")
    graph.add_edge("critic", "arbiter")
    graph.add_conditional_edges(
        "arbiter",
        route_from_arbiter,
        {"gate": "gate", "coder": "coder", END: END},
    )
    graph.add_edge("gate", "record_gate")
    graph.add_conditional_edges(
        "record_gate",
        route_from_record_gate,
        {"runner": "runner", END: END},
    )
    graph.add_conditional_edges(
        "runner",
        route_from_runner,
        {"coder": "coder", END: END},
    )
    return graph.compile(checkpointer=MemorySaver())


def run_session(
    graph,
    *,
    task: str,
    thread_id: str,
    decide: Callable[[dict[str, Any]], str] | None = None,
) -> list[dict[str, Any]]:
    """Stream the session; when the gate interrupts, ask ``decide`` (default reject).

    ``decide`` receives the interrupt payload and must return 'approve'/'reject'.
    """
    decide = decide or (lambda _payload: GATE_REJECT)  # fail-closed: nothing applies silently
    config = {"configurable": {"thread_id": thread_id}}
    initial: SessionState = {
        "task": task,
        "patch": "",
        "attack": "",
        "critique": "",
        "coder_rounds": 0,
        "adversary_rounds": 0,
        "thread_id": thread_id,
        "last_event_id": None,
        "verdict": "",
        "gate_decision": None,
        "escalated": [],
        "test_passed": None,
    }
    seen: list[dict[str, Any]] = []
    for update in graph.stream(initial, config=config, stream_mode="updates"):
        seen.append(update)
        if "__interrupt__" in update:
            payload = update["__interrupt__"][0].value
            for cont in graph.stream(
                Command(resume=decide(payload)), config=config, stream_mode="updates"
            ):
                seen.append(cont)
    return seen
