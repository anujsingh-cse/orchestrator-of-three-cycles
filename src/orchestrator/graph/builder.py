"""Graph builder (T3) — LangGraph StateGraph with HITL gate + routing table.

Flow (design doc diagram):
START -> recon -> coder -> adversary -> critic -> arbiter -> gate -> record_gate -> runner -> END
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
from pathlib import Path
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


def make_recon(worktree_root: Path) -> NodeFn:
    """Recon node: index worktree and retrieve relevant code for the task.

    Uses the hybrid retrieval store (dense + sparse vectors with RRF fusion)
    to find code chunks relevant to the task description.
    """
    from orchestrator.retrieval.chunker import Chunker
    from orchestrator.retrieval.store import RetrievalQuery, RetrievalStore

    # Initialize chunker and store (lazy, on first call)
    _chunker: Chunker | None = None
    _store: RetrievalStore | None = None
    _indexed = False

    def _init():
        nonlocal _chunker, _store, _indexed
        if _indexed:
            return
        try:
            _chunker = Chunker()
            # Use main repo's .orchestrator/retrieval for persistent index across sessions
            retrieval_path = worktree_root.parent.parent / ".orchestrator" / "retrieval"
            _store = RetrievalStore(retrieval_path)

            # Index all Python/JS/TS files in worktree
            import subprocess
            result = subprocess.run(
                ["git", "-C", str(worktree_root), "ls-files"],
                capture_output=True, text=True, check=True
            )
            all_files = [worktree_root / f for f in result.stdout.strip().split("\n") if f]
            suffixes = (".py", ".js", ".ts", ".mjs", ".jsx", ".tsx")
            code_files = [f for f in all_files if f.suffix in suffixes]

            if code_files:
                chunks = _chunker.chunk_files(code_files)
                if chunks:
                    _store.upsert_chunks(chunks)

            _indexed = True
        except Exception as e:
            # If retrieval fails, continue without it
            print(f"[recon] indexing failed: {e}")
            _chunker = None
            _store = None
            _indexed = True

    def recon(state: SessionState) -> dict[str, Any]:
        _init()

        file_context = {}
        if _store and _chunker:
            try:
                # Query with the task description
                query = RetrievalQuery(text=state["task"], top_k=8)
                results = _store.query(query)

                # Group by file for context
                for r in results:
                    chunk = r.chunk
                    rel_path = chunk.file_path
                    if rel_path not in file_context:
                        file_context[rel_path] = []
                    file_context[rel_path].append({
                        "lines": f"{chunk.start_line}-{chunk.end_line}",
                        "fqn": chunk.fqn,
                        "signature": chunk.signature,
                        "content": chunk.content,
                    })

                # Format for prompt
                formatted = {}
                for path, chunks in file_context.items():
                    parts = []
                    for c in chunks:
                        parts.append(f"# {c['lines']} {c['fqn'] or ''} {c['signature'] or ''}")
                        parts.append(c['content'])
                    formatted[path] = "\n\n".join(parts)

                return {"file_context": formatted}
            except Exception as e:
                print(f"[recon] query failed: {e}")

        return {"file_context": {}}

    return recon


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
    graph.add_node("recon", make_recon(Path(worktree_root)))
    graph.add_node("coder", make_coder(adapters["coder"], sink, ids["coder"]))
    graph.add_node("adversary", make_adversary(adapters["adversary"], sink, ids["adversary"]))
    graph.add_node("critic", make_critic(adapters["critic"], sink, ids["critic"]))
    graph.add_node("arbiter", make_arbiter(adapters["arbiter"], sink, ids["arbiter"]))
    graph.add_node("gate", make_gate())
    graph.add_node("record_gate", make_record_gate(sink, "human-gate"))
    graph.add_node("runner", make_runner(worktree_root, sink, ids.get("runner", "runner")))

    graph.add_edge(START, "recon")
    graph.add_edge("recon", "coder")
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
