"""Control arm — flat single-shot loop (T10, D17).

The flat loop is the comparison baseline: Coder → tests → verdict,
with NO Adversary/Critic cycles. Same adapter/audit/runner/corpus.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orchestrator.adapters.llm.base import LLMAdapter
from orchestrator.audit.event import AuditEvent
from orchestrator.audit.sink import AuditSink
from orchestrator.graph.prompts import coder_prompt
from orchestrator.runner.diff import apply_patch
from orchestrator.runner.process import run_tests


@dataclass(frozen=True)
class FlatLoopResult:
    """Result of a flat loop execution."""

    task: str
    patch: str
    verdict: str  # pass | fail | error
    test_passed: bool | None
    coder_rounds: int
    tokens_total: int
    audit_event_ids: list[str]
    error: str | None = None


class FlatLoop:
    """Flat single-shot loop: Coder proposes → tests run → verdict.

    No Adversary, no Critic, no Arbiter. Just one shot at the patch.
    Uses the same LLM adapter, audit sink, and runner as the falsification loop.
    """

    def __init__(
        self,
        coder: LLMAdapter,
        sink: AuditSink,
        source_repo: Path,
        bases_root: Path,
        agent_id: str = "flat-coder",
        max_rounds: int = 1,
    ) -> None:
        self.coder = coder
        self.sink = sink
        self.source_repo = source_repo
        self.bases_root = bases_root
        self.agent_id = agent_id
        self.max_rounds = max_rounds

    def _create_worktree(self, thread_id: str) -> Path:
        """Create a git worktree for isolated execution."""
        self.bases_root.mkdir(parents=True, exist_ok=True)
        target = self.bases_root / thread_id
        if target.exists():
            shutil.rmtree(target)
        cmd = [
            "git",
            "-C",
            str(self.source_repo),
            "worktree",
            "add",
            "--detach",
            str(target),
            "HEAD",
        ]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"worktree add failed: {proc.stderr.strip()}")
        return target

    def _remove_worktree(self, worktree_root: Path) -> None:
        cmd = [
            "git",
            "-C",
            str(self.source_repo),
            "worktree",
            "remove",
            "--force",
            str(worktree_root),
        ]
        subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def run(
        self,
        task: str,
        repo_url: str = "",
        license: str = "",
        commit_sha: str = "",
        bug_type: str = "",
    ) -> FlatLoopResult:
        """Execute the flat loop for a single task."""
        provenance = {
            "repo_url": repo_url,
            "license": license,
            "commit_sha": commit_sha,
            "bug_type": bug_type,
        }

        audit_ids: list[str] = []
        tokens_total = 0
        patch = ""
        test_passed: bool | None = None
        verdict = "error"
        error: str | None = None

        try:
            for round_no in range(1, self.max_rounds + 1):
                # Coder proposes patch
                messages = coder_prompt(task, "", "", round_no)
                response = self.coder.complete(messages)
                tokens_total += response.tokens_in + response.tokens_out
                patch = response.text.strip()

                # Write audit event for coder
                payload_in = {"task": task, "round": round_no, **provenance}
                event = AuditEvent(
                    parent_event_id=audit_ids[-1] if audit_ids else None,
                    thread_id=f"flat-{self.agent_id}-{hash(task) % 1000000}",
                    node="coder",
                    agent_id=self.agent_id,
                    model_id=self.coder.model_id,
                    input_hash=AuditEvent.content_hash(payload_in),
                    output_hash=AuditEvent.content_hash({"patch": patch}),
                    payload_in=payload_in,
                    payload_out={"patch": patch},
                    tool_calls=[],
                    tokens_in=response.tokens_in,
                    tokens_out=response.tokens_out,
                )
                audit_id = self.sink.write_ahead(event)
                audit_ids.append(audit_id)

            # Apply and test the patch
            thread_id = f"flat-{self.agent_id}-{hash(task) % 1000000}"
            worktree_root = self._create_worktree(thread_id)
            try:
                apply_result = apply_patch(worktree_root, patch)

                tool_calls: list[dict[str, Any]] = [
                    {"name": "git apply", "input": {}, "output": apply_result.__dict__}
                ]

                if apply_result.status == "applied":
                    test_result = run_tests(worktree_root)
                    test_dict = test_result.__dict__
                    tool_calls.append({"name": "run tests", "input": {}, "output": test_dict})
                    test_passed = test_result.passed
                    verdict = "pass" if test_result.passed else "fail"
                else:
                    verdict = "fail"
                    test_passed = False

                output_hash = AuditEvent.content_hash(
                    {"apply": apply_result.status, "test_passed": test_passed}
                )
                runner_event = AuditEvent(
                    parent_event_id=audit_ids[-1] if audit_ids else None,
                    thread_id=thread_id,
                    node="runner",
                    agent_id=f"{self.agent_id}-runner",
                    model_id="",
                    input_hash=AuditEvent.content_hash({"patch": patch}),
                    output_hash=output_hash,
                    payload_in={"patch": patch},
                    payload_out={"apply": apply_result.status, "test_passed": test_passed},
                    tool_calls=tool_calls,
                    tokens_in=0,
                    tokens_out=0,
                )
                runner_id = self.sink.write_ahead(runner_event)
                audit_ids.append(runner_id)

            finally:
                self._remove_worktree(worktree_root)

        except Exception as exc:
            error = str(exc)
            verdict = "error"

        return FlatLoopResult(
            task=task,
            patch=patch,
            verdict=verdict,
            test_passed=test_passed,
            coder_rounds=self.max_rounds,
            tokens_total=tokens_total,
            audit_event_ids=audit_ids,
            error=error,
        )
