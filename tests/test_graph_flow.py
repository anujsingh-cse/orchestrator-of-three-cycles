"""T3 end-to-end graph flow tests — FakeAdapters, real LangGraph, real git.

Each scenario runs the compiled falsification loop against a fixture repo in a
throwaway worktree; the audit DAG is the oracle (D2/D7/D11).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from orchestrator.adapters.llm.base import LLMAdapter, LLMResponse
from orchestrator.audit.sink import AuditSink
from orchestrator.graph.builder import GATE_APPROVE, build_session_graph, run_session
from orchestrator.runner.worktree import create_worktree

MAIN_OLD = "def add(a, b):\n    return a + b\n"
MAIN_NEW = "def add(a, b):\n    return a + b\n\n\ndef helper() -> int:\n    return 42\n"
TEST_FILE = "def test_add():\n    from main import add\n\n    assert add(1, 2) == 3\n"


class FakeAdapter(LLMAdapter):
    def __init__(self, responses: list[str], model_id: str) -> None:
        super().__init__()
        self.responses = list(responses)
        self.model_id = model_id
        self.calls: list[list[dict[str, str]]] = []

    def complete(self, messages: list[dict[str, str]], **kwargs) -> LLMResponse:
        self.calls.append(messages)
        text = self.responses.pop(0)
        return LLMResponse(
            text=text,
            finish_reason="stop",
            tokens_in=len(text) // 4,
            tokens_out=len(text) // 4,
            model_id=self.model_id,
        )

    def stream(self, messages, **kwargs):  # pragma: no cover - unused in tests
        raise NotImplementedError

    async def astream(self, messages, **kwargs):  # pragma: no cover - unused in tests
        raise NotImplementedError

    def health(self) -> bool:
        return True


def _make_patch(repo: Path) -> str:
    (repo / "main.py").write_text(MAIN_OLD, encoding="utf-8", newline="\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
    (repo / "main.py").write_text(MAIN_NEW, encoding="utf-8", newline="\n")
    diff = subprocess.run(
        ["git", "-C", str(repo), "diff"], capture_output=True, text=True, check=True
    ).stdout
    subprocess.run(["git", "-C", str(repo), "checkout", "--", "main.py"], check=True)
    return diff


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "t"], check=True)
    (tmp_path / "main.py").write_text(MAIN_OLD, encoding="utf-8", newline="\n")
    (tmp_path / "test_main.py").write_text(TEST_FILE, encoding="utf-8", newline="\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "init"], check=True)
    return tmp_path


def _graph(repo: Path, sink: AuditSink, patch: str):
    return build_session_graph(
        adapters={
            "coder": FakeAdapter([patch], "fake-coder"),
            "adversary": FakeAdapter(["No flaws found."], "fake-adversary"),
            "critic": FakeAdapter(["The patch is sound. No defects."], "fake-critic"),
            "arbiter": FakeAdapter(["verdict: pass\nit solves the task"], "fake-arbiter"),
        },
        sink=sink,
        worktree_root=create_worktree(repo, repo.parent / "worktrees", "wt-1"),
    )


def _event_nodes(sink: AuditSink, thread_id: str) -> list[str]:
    return [e.node for e in sink.session_events(thread_id)]


class TestHappyPath:
    def test_full_loop_passes_and_chains_dag(self, repo: Path, tmp_path: Path) -> None:
        sink = AuditSink(tmp_path / "audit.db")
        patch = _make_patch(repo)
        graph = _graph(repo, sink, patch)
        updates = run_session(
            graph, task="add helper()", thread_id="t1", decide=lambda _p: GATE_APPROVE
        )
        node_names = [list(u)[0] for u in updates if "__interrupt__" not in u]
        assert node_names == [
            "coder",
            "adversary",
            "critic",
            "arbiter",
            "gate",
            "record_gate",
            "runner",
        ]

        sink.verify_integrity("t1")  # causal chain intact (D2)
        events = sink.session_events("t1")
        assert _event_nodes(sink, "t1") == [
            "coder",
            "adversary",
            "critic",
            "arbiter",
            "gate",
            "runner",
        ]
        assert events[-1].node == "runner"
        assert events[-1].gate_decision == GATE_APPROVE
        assert events[-1].tool_calls[0]["name"] == "git apply"
        assert events[0].parent_event_id is None
        for e in events[1:]:
            assert e.parent_event_id is not None


class TestGate:
    def test_reject_records_decision_and_never_runs_runner(
        self, repo: Path, tmp_path: Path
    ) -> None:
        sink = AuditSink(tmp_path / "audit.db")
        patch = _make_patch(repo)
        graph = _graph(repo, sink, patch)
        updates = run_session(
            graph, task="add helper()", thread_id="t1", decide=lambda _p: "reject"
        )
        runner_seen = any(list(u)[0] == "runner" for u in updates)
        assert not runner_seen
        sink.verify_integrity("t1")
        events = sink.session_events("t1")
        assert _event_nodes(sink, "t1") == ["coder", "adversary", "critic", "arbiter", "gate"]
        assert events[-1].gate_decision == "reject"

    def test_default_decision_is_fail_closed_reject(self, repo: Path, tmp_path: Path) -> None:
        sink = AuditSink(tmp_path / "audit.db")
        graph = _graph(repo, sink, _make_patch(repo))
        run_session(graph, task="add helper()", thread_id="t1")
        events = sink.session_events("t1")
        assert events[-1].gate_decision == "reject"
        assert events[-1].node == "gate"


class TestBudget:
    def test_retry_verdict_burns_coder_budget_then_exhausts(
        self, repo: Path, tmp_path: Path
    ) -> None:
        sink = AuditSink(tmp_path / "audit.db")
        patch = _make_patch(repo)
        graph = build_session_graph(
            adapters={
                "coder": FakeAdapter([patch] * 5, "fake-coder"),
                "adversary": FakeAdapter(["flaw A"] * 4, "fake-adversary"),
                "critic": FakeAdapter(["defect 1"] * 5, "fake-critic"),
                "arbiter": FakeAdapter(["verdict: patch_fix\nround again"] * 5, "fake-arbiter"),
            },
            sink=sink,
            worktree_root=create_worktree(repo, repo.parent / "worktrees", "wt-2"),
        )
        updates = run_session(graph, task="add helper()", thread_id="t1")
        nodes = [list(u)[0] for u in updates]
        assert nodes.count("coder") == 5
        assert nodes.count("adversary") == 4  # skipped once budget spent
        assert "gate" not in nodes  # budget_exhausted never reaches the human
        arbiter_events = [e for e in sink.session_events("t1") if e.node == "arbiter"]
        assert len(arbiter_events) == 5
        assert arbiter_events[-1].node == "arbiter"
        sink.verify_integrity("t1")


class TestEscalation:
    def test_guard_railed_path_forces_escalate_and_human_gate(
        self, repo: Path, tmp_path: Path
    ) -> None:
        (repo / ".env").write_text("KEY=1\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "add env"], check=True)
        (repo / ".env").write_text("KEY=2\n", encoding="utf-8")
        env_patch = subprocess.run(
            ["git", "-C", str(repo), "diff"], capture_output=True, text=True, check=True
        ).stdout
        subprocess.run(["git", "-C", str(repo), "checkout", "--", ".env"], check=True)
        assert ".env" in env_patch

        sink = AuditSink(tmp_path / "audit.db")
        graph = build_session_graph(
            adapters={
                "coder": FakeAdapter([env_patch], "fake-coder"),
                "adversary": FakeAdapter(["n/a"], "fake-adversary"),
                "critic": FakeAdapter(["ok"], "fake-critic"),
                "arbiter": FakeAdapter(["verdict: pass\nship it"], "fake-arbiter"),
            },
            sink=sink,
            worktree_root=create_worktree(repo, repo.parent / "worktrees", "wt-3"),
        )
        interrupted: list[dict] = []
        run_session(
            graph,
            task="rotate key",
            thread_id="t1",
            decide=lambda payload: interrupted.append(payload) or "reject",
        )
        assert interrupted, "gate must interrupt on an escalated patch"
        assert interrupted[0]["escalated"] == [".env"]
        arbiter_event = [e for e in sink.session_events("t1") if e.node == "arbiter"][0]
        assert arbiter_event.output_hash  # recorded, D7
        sink.verify_integrity("t1")
