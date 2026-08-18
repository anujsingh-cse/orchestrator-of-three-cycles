"""D11 release-blocking: HITL resume must NOT re-execute side effects.

LangGraph gotcha under test: resumed nodes replay from the top of the node;
the gate node must be side-effect-free, and the coder must not re-run when
the thread resumes via Command(resume=...).
"""

from __future__ import annotations

from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import Command
from langgraph.types import interrupt

from orchestrator.adapters.llm.fake import FakeAdapter


class GraphState(TypedDict):
    task: str
    patch: str
    verdict: str


def build_graph(coder: FakeAdapter, counter: dict[str, int]) -> StateGraph:
    graph = StateGraph(GraphState)

    def coder_node(state: GraphState) -> dict[str, str]:
        counter["coder_runs"] += 1
        return {"patch": coder.complete([{"role": "user", "content": state["task"]}]).text}

    def gate_node(state: GraphState) -> dict[str, str]:
        decision: str = interrupt("approve patch?")
        return {"verdict": f"gate:{decision}"}

    graph.add_node("coder", coder_node)
    graph.add_node("gate", gate_node)
    graph.add_edge(START, "coder")
    graph.add_edge("coder", "gate")
    graph.add_edge("gate", END)
    return graph


def test_hitl_resume_does_not_re_execute(tmp_path) -> None:
    coder = FakeAdapter("fake-model")
    counter = {"coder_runs": 0}
    with SqliteSaver.from_conn_string(str(tmp_path / "ckpt.sqlite")) as checkpointer:
        graph = build_graph(coder, counter).compile(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": "hitl-1"}}
        list(graph.stream({"task": "fix bug"}, config, stream_mode="updates"))

        snap = graph.get_state(config)
        assert bool(snap.next) and bool(snap.interrupts)  # paused at gate

        list(graph.stream(Command(resume="approved"), config, stream_mode="updates"))
        final = graph.get_state(config).values

    assert counter["coder_runs"] == 1  # resumed: side effect NOT re-executed
    assert final["verdict"] == "gate:approved"
    assert len(coder.calls) == 1  # single LLM call despite two stream passes
