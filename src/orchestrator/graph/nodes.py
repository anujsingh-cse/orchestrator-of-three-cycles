"""Graph nodes (T3) — agent nodes are audit-wired closures over (adapter, sink).

Every node writes its AuditEvent write-ahead BEFORE returning the state
transition (D7); the returned ``last_event_id`` chains the DAG. Gate nodes are
side-effect-free (D11): interrupt() bodies may re-run on resume, so they never
touch the sink or the filesystem.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from orchestrator.adapters.llm.base import LLMAdapter
from orchestrator.audit.event import AuditEvent
from orchestrator.audit.sink import AuditSink
from orchestrator.graph.prompts import adversary_prompt, arbiter_prompt, coder_prompt, critic_prompt
from orchestrator.graph.state import SessionState
from orchestrator.graph.verdict import MAX_CODER_ROUNDS, Verdict, escalation_hit

NodeFn = Callable[[SessionState], dict[str, Any]]

VERDICT_LINE = re.compile(r"^\s*verdict:\s*(\S+)", re.MULTILINE)


def _emit(
    sink: AuditSink,
    state: SessionState,
    node: str,
    agent_id: str,
    model_id: str,
    payload_in: dict[str, Any],
    payload_out: dict[str, Any],
    tokens_in: int = 0,
    tokens_out: int = 0,
    tool_calls: list[dict[str, Any]] | None = None,
    gate_decision: str | None = None,
) -> str:
    event = AuditEvent(
        parent_event_id=state.get("last_event_id"),
        thread_id=state["thread_id"],
        node=node,
        agent_id=agent_id,
        model_id=model_id,
        input_hash=AuditEvent.content_hash(payload_in),
        output_hash=AuditEvent.content_hash(payload_out),
        tool_calls=tool_calls or [],
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        gate_decision=gate_decision,
    )
    return sink.write_ahead(event)  # fail-closed: raises on write failure


def _strip_fence(text: str) -> str:
    """Pull a unified diff out of ```diff ... ``` fences if present."""
    match = re.search(r"```diff\s*\n(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text.strip()


def _parse_verdict(text: str) -> Verdict:
    match = VERDICT_LINE.search(text)
    if not match:
        return Verdict.ESCALATE  # malformed arbiter output -> fail-closed to human
    try:
        return Verdict(match.group(1))
    except ValueError:
        return Verdict.ESCALATE


def make_coder(adapter: LLMAdapter, sink: AuditSink, agent_id: str) -> NodeFn:
    def coder(state: SessionState) -> dict[str, Any]:
        round_no = state.get("coder_rounds", 0) + 1
        messages = coder_prompt(
            state["task"], state.get("attack", ""), state.get("critique", ""), round_no
        )
        response = adapter.complete(messages)
        patch = _strip_fence(response.text)
        event_id = _emit(
            sink,
            state,
            node="coder",
            agent_id=agent_id,
            model_id=adapter.model_id,
            payload_in={"task": state["task"], "round": round_no},
            payload_out={"patch": patch},
            tokens_in=response.tokens_in,
            tokens_out=response.tokens_out,
        )
        return {"patch": patch, "coder_rounds": round_no, "last_event_id": event_id}

    return coder


def make_adversary(adapter: LLMAdapter, sink: AuditSink, agent_id: str) -> NodeFn:
    def adversary(state: SessionState) -> dict[str, Any]:
        round_no = state.get("adversary_rounds", 0) + 1
        messages = adversary_prompt(state["patch"])
        response = adapter.complete(messages)
        event_id = _emit(
            sink,
            state,
            node="adversary",
            agent_id=agent_id,
            model_id=adapter.model_id,
            payload_in={"patch": state["patch"]},
            payload_out={"attack": response.text},
            tokens_in=response.tokens_in,
            tokens_out=response.tokens_out,
        )
        return {"attack": response.text, "adversary_rounds": round_no, "last_event_id": event_id}

    return adversary


def make_critic(adapter: LLMAdapter, sink: AuditSink, agent_id: str) -> NodeFn:
    def critic(state: SessionState) -> dict[str, Any]:
        messages = critic_prompt(state["task"], state["patch"], state.get("attack", ""))
        response = adapter.complete(messages)
        event_id = _emit(
            sink,
            state,
            node="critic",
            agent_id=agent_id,
            model_id=adapter.model_id,
            payload_in={
                "task": state["task"],
                "patch": state["patch"],
                "attack": state.get("attack", ""),
            },
            payload_out={"critique": response.text},
            tokens_in=response.tokens_in,
            tokens_out=response.tokens_out,
        )
        return {"critique": response.text, "last_event_id": event_id}

    return critic


def make_arbiter(adapter: LLMAdapter, sink: AuditSink, agent_id: str) -> NodeFn:
    def arbiter(state: SessionState) -> dict[str, Any]:
        coder_rounds = state.get("coder_rounds", 0)
        messages = arbiter_prompt(
            state["task"],
            state["patch"],
            state.get("attack", ""),
            state.get("critique", ""),
            coder_rounds,
        )
        response = adapter.complete(messages)
        verdict = _parse_verdict(response.text)
        escalated = escalation_hit(state["patch"])
        if coder_rounds >= MAX_CODER_ROUNDS and verdict in (
            Verdict.PATCH_FIX,
            Verdict.REPLAN,
            Verdict.MINOR_FIX,
        ):
            verdict = Verdict.BUDGET_EXHAUSTED  # loop budget spent (design doc)
        if escalated:
            verdict = Verdict.ESCALATE  # guard-railed path -> human gate (D5)
        event_id = _emit(
            sink,
            state,
            node="arbiter",
            agent_id=agent_id,
            model_id=adapter.model_id,
            payload_in={
                "patch": state["patch"],
                "attack": state.get("attack", ""),
                "rounds": coder_rounds,
            },
            payload_out={"verdict": verdict.value, "escalated": escalated},
            tokens_in=response.tokens_in,
            tokens_out=response.tokens_out,
        )
        return {"verdict": verdict.value, "escalated": escalated, "last_event_id": event_id}

    return arbiter


def make_runner(worktree_root: Any, sink: AuditSink, agent_id: str) -> NodeFn:
    """Apply + verify the patch in an isolated worktree (D5/D8).

    IRREVERSIBLE — runs only after the HITL gate approves; its gate_decision
    is recorded on its own event (the gate itself never writes).
    """
    from dataclasses import asdict

    from orchestrator.runner.diff import apply_patch
    from orchestrator.runner.process import run_tests

    def runner(state: SessionState) -> dict[str, Any]:
        apply_result = apply_patch(worktree_root, state["patch"])
        tool_calls: list[dict[str, Any]] = [
            {"name": "git apply", "input": {}, "output": asdict(apply_result)}
        ]
        test_passed: bool | None = None
        if apply_result.status == "applied":
            test_result = run_tests(worktree_root)
            tool_calls.append({"name": "run tests", "input": {}, "output": asdict(test_result)})
            test_passed = test_result.passed
        event_id = _emit(
            sink,
            state,
            node="runner",
            agent_id=agent_id,
            model_id="",
            payload_in={"patch": state["patch"]},
            payload_out={"apply": apply_result.status, "test_passed": test_passed},
            tool_calls=tool_calls,
            gate_decision=state.get("gate_decision"),
        )
        return {"test_passed": test_passed, "last_event_id": event_id}

    return runner
