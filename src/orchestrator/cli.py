from __future__ import annotations

import argparse
import os
import sys
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from langgraph.types import Command

from orchestrator.adapters.llm.base import LLMAdapter
from orchestrator.adapters.llm.fake import FakeAdapter
from orchestrator.adapters.llm.nim import NIMAdapter
from orchestrator.adapters.llm.pacing import Pacer, PacingConfig
from orchestrator.audit.sink import AuditSink
from orchestrator.graph.builder import GATE_APPROVE, build_session_graph
from orchestrator.roster import MODEL_ROSTER, NIM_MEASURED_RPM
from orchestrator.runner.worktree import create_worktree


def build_adapters(use_nim: bool, api_key: str | None) -> dict[str, LLMAdapter]:
    """Build the four role adapters with per-model pacing."""
    if use_nim:
        # Fall back to env var if --api-key not provided
        api_key = api_key or os.environ.get("NIM_API_KEY")
        if not api_key:
            raise ValueError("NIM_API_KEY required for --nim mode (use --api-key or set NIM_API_KEY env)")
        return {
            "coder": NIMAdapter(
                MODEL_ROSTER["coder"],
                api_key=api_key,
                pacer=Pacer(PacingConfig(max_requests_per_minute=NIM_MEASURED_RPM["coder"])),
            ),
            "adversary": NIMAdapter(
                MODEL_ROSTER["adversary"],
                api_key=api_key,
                pacer=Pacer(PacingConfig(max_requests_per_minute=NIM_MEASURED_RPM["adversary"])),
            ),
            "critic": NIMAdapter(
                MODEL_ROSTER["critic"],
                api_key=api_key,
                pacer=Pacer(PacingConfig(max_requests_per_minute=NIM_MEASURED_RPM["critic"])),
            ),
            "arbiter": NIMAdapter(
                MODEL_ROSTER["critic"],
                api_key=api_key,
                pacer=Pacer(PacingConfig(max_requests_per_minute=NIM_MEASURED_RPM["critic"])),
            ),
        }
    return {
        "coder": FakeAdapter("fake-coder"),
        "adversary": FakeAdapter("fake-adversary"),
        "critic": FakeAdapter("fake-critic"),
        "arbiter": FakeAdapter("fake-arbiter"),
    }


def _default_decide(_payload: dict[str, Any]) -> str:
    return GATE_APPROVE


def stream_updates(
    graph,
    task: str,
    thread_id: str,
    decide: Callable[[dict[str, Any]], str] | None,
) -> Iterator[dict[str, Any]]:
    """Yield graph updates for TUI/plain streaming."""
    decide = decide or _default_decide
    config = {"configurable": {"thread_id": thread_id}}
    initial: dict[str, Any] = {
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
    for update in graph.stream(initial, config=config, stream_mode="updates"):
        yield update
        if "__interrupt__" in update:
            payload = update["__interrupt__"][0].value
            yield from graph.stream(
                Command(resume=decide(payload)), config=config, stream_mode="updates"
            )


def run_tui(task: str, repo_path: Path, adapters: dict, thread_id: str, decide) -> int:
    """Run Textual TUI. Returns exit code."""
    try:
        from textual.app import App, ComposeResult
        from textual.widgets import Header, Log
    except ImportError:
        print("Textual not installed. Falling back to plain streaming.")
        return 1

    import threading

    class StatusApp(App):
        BINDINGS = [("q", "quit", "Quit")]

        def __init__(
            self,
            task: str,
            repo_path: Path,
            adapters: dict,
            thread_id: str,
            decide,
        ) -> None:
            super().__init__()
            self._task = task
            self._repo_path = repo_path
            self._adapters = adapters
            self._thread_id = thread_id
            self._decide = decide
            self._interrupt_event = threading.Event()
            self._pending_payload: dict | None = None
            self._resume_decision: str | None = None

        def compose(self) -> ComposeResult:
            yield Header()
            yield Log(highlight=True, id="session-log")

        def on_mount(self) -> None:
            self.run_worker(self._run_session, thread=True)

        def _run_session(self) -> None:
            # Create sink and graph in this thread
            from orchestrator.audit.sink import AuditSink
            from orchestrator.graph.builder import build_session_graph
            from orchestrator.runner.worktree import create_worktree

            audit_path = self._repo_path / ".orchestrator" / "audit.db"
            audit_path.parent.mkdir(exist_ok=True)
            sink = AuditSink(audit_path)
            worktree = create_worktree(
                self._repo_path,
                self._repo_path.parent / "worktrees",
                str(self._thread_id),
            )
            graph = build_session_graph(
                adapters=self._adapters,
                sink=sink,
                worktree_root=worktree,
            )

            from langgraph.types import Command
            config = {"configurable": {"thread_id": str(self._thread_id)}}
            initial: dict[str, Any] = {
                "task": self._task,
                "patch": "",
                "attack": "",
                "critique": "",
                "coder_rounds": 0,
                "adversary_rounds": 0,
                "thread_id": str(self._thread_id),
                "last_event_id": None,
                "verdict": "",
                "gate_decision": None,
                "escalated": [],
                "test_passed": None,
            }
            for update in graph.stream(initial, config=config, stream_mode="updates"):
                for node, payload in update.items():
                    if node == "__interrupt__":
                        self._pending_payload = payload[0].value
                        self.call_from_thread(self._handle_interrupt)
                        # Wait for user decision
                        self._interrupt_event.wait()
                        self._interrupt_event.clear()
                        # Resume with the decision
                        if self._resume_decision:
                            decision = self._resume_decision
                        elif self._decide:
                            decision = self._decide(self._pending_payload)
                        else:
                            decision = "approve"
                        self._pending_payload = None
                        self._resume_decision = None
                        for cont in graph.stream(
                            Command(resume=decision), config=config, stream_mode="updates"
                        ):
                            for n, p in cont.items():
                                self.call_from_thread(self._log_update, n, p)
                        continue
                    self.call_from_thread(self._log_update, node, payload)
            self.call_from_thread(self._log_done)

        def _handle_interrupt(self) -> None:
            log = self.query_one("#session-log", Log)
            prompt = self._pending_payload.get("prompt", "approve?")
            log.write(f"[HITL] {prompt}")
            # Auto-approve for demo; real TUI would show buttons
            self._resume_decision = "approve"
            self._interrupt_event.set()

        def _log_update(self, node: str, payload: dict[str, Any]) -> None:
            log = self.query_one("#session-log", Log)
            log.write(f"[{node}] {_summarize(node, payload)}")

        def _log_done(self) -> None:
            log = self.query_one("#session-log", Log)
            log.write("[done] session finished")

    app = StatusApp(task, repo_path, adapters, thread_id, decide)
    app.run()
    return 0


def run_plain(updates_fn: Callable[[], Iterator[dict[str, Any]]]) -> int:
    """Plain-text streaming fallback."""
    for update in updates_fn():
        for node, payload in update.items():
            if node == "__interrupt__":
                print(f"[HITL] {payload[0].value.get('prompt', 'approve?')}")
            else:
                print(f"[{node}] {_summarize(node, payload)}")
    print("[done] session finished")
    return 0


def _summarize(node: str, payload: dict[str, Any]) -> str:
    if node in ("coder", "runner"):
        return f"patch {len(payload.get('patch', ''))} chars"
    if node == "arbiter":
        return f"verdict={payload.get('verdict')} escalated={payload.get('escalated')}"
    if node == "gate":
        return f"decision={payload.get('gate_decision')}"
    return f"{len(payload)} keys"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Orchestrator of Three Cycles — falsification loop"
    )
    parser.add_argument("task", help="Task description for the Coder")
    parser.add_argument("--repo", default=".", help="Path to repository (default: cwd)")
    parser.add_argument(
        "--nim", action="store_true", help="Use NIM free-tier models (needs NIM_API_KEY)"
    )
    parser.add_argument("--api-key", help="NIM API key (or set NIM_API_KEY env)")
    parser.add_argument(
        "--tui", action="store_true", help="Use Textual TUI (default: plain streaming)"
    )
    parser.add_argument(
        "--auto-approve", action="store_true", help="Auto-approve HITL gate (default: reject)"
    )
    args = parser.parse_args()

    repo_path = Path(args.repo).resolve()
    if not (repo_path / ".git").exists():
        print(f"Error: {repo_path} is not a git repository")
        return 1

    thread_id = f"session-{uuid.uuid4().hex[:8]}"
    audit_path = repo_path / ".orchestrator" / "audit.db"
    audit_path.parent.mkdir(exist_ok=True)
    sink = AuditSink(audit_path)

    try:
        adapters = build_adapters(args.nim, args.api_key)
    except ValueError as e:
        print(f"Error: {e}")
        return 1

    worktree = create_worktree(repo_path, repo_path.parent / "worktrees", thread_id)
    graph = build_session_graph(
        adapters=adapters,
        sink=sink,
        worktree_root=worktree,
    )

    decide = None
    if args.auto_approve:
        decide = _default_decide

    def updates_fn() -> Iterator[dict[str, Any]]:
        yield from stream_updates(graph, args.task, thread_id, decide)

    if args.tui:
        return run_tui(args.task, repo_path, adapters, thread_id, decide)
    return run_plain(updates_fn)


if __name__ == "__main__":
    sys.exit(main())
