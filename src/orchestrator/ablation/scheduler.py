"""Ablation runner — bounded parallel lanes (T11, D16, D23).

Executes falsification loop and flat control arm across the seed corpus
in parallel lanes, with lane count bounded by the adapter pacing budget (D6).
Per-repo state is disjoint (D5).
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from orchestrator.ablation.control_arm import FlatLoop
from orchestrator.ablation.corpus import SeedBug, SeedCorpus
from orchestrator.adapters.llm.pacing import PacingConfig
from orchestrator.audit.sink import AuditSink
from orchestrator.graph.builder import build_session_graph
from orchestrator.runner.worktree import WorktreeManager


@dataclass(frozen=True)
class LaneResult:
    """Result from a single ablation lane."""

    bug: SeedBug
    mode: str  # "falsification" or "flat"
    success: bool
    verdict: str
    test_passed: bool | None
    tokens_total: int
    elapsed_s: float
    error: str | None = None
    audit_event_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AblationRun:
    """Complete ablation run across the corpus."""

    run_id: str
    timestamp: str
    lane_results: list[LaneResult]
    config: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "config": self.config,
            "lane_results": [asdict(lr) for lr in self.lane_results],
        }


class AblationScheduler:
    """Schedules and runs ablation lanes with bounded parallelism.

    - Lanes are bounded by the adapter pacing budget (D16, D6)
    - Each lane gets its own worktree, audit DB, and adapter instances
    - Runs falsification loop AND flat control arm for each bug
    """

    def __init__(
        self,
        corpus: SeedCorpus,
        source_repos: dict[str, Path],  # repo_url -> local path
        bases_root: Path,
        audit_root: Path,
        coder_adapter_factory,  # callable() -> LLMAdapter
        adversary_adapter_factory,  # callable() -> LLMAdapter
        critic_adapter_factory,  # callable() -> LLMAdapter
        arbiter_adapter_factory,  # callable() -> LLMAdapter
        pacing_configs: dict[str, PacingConfig],
        max_parallel_lanes: int | None = None,
    ) -> None:
        self.corpus = corpus
        self.source_repos = source_repos
        self.bases_root = bases_root
        self.audit_root = audit_root
        self.coder_adapter_factory = coder_adapter_factory
        self.adversary_adapter_factory = adversary_adapter_factory
        self.critic_adapter_factory = critic_adapter_factory
        self.arbiter_adapter_factory = arbiter_adapter_factory
        self.pacing_configs = pacing_configs

        # Compute max lanes from pacing budget (D16)
        if max_parallel_lanes is None:
            # Sum of all model RPM ceilings / (models per lane * safety factor)
            # Each lane uses 4 models (coder, adversary, critic, arbiter)
            total_rpm = sum(config.max_requests_per_minute for config in pacing_configs.values())
            max_parallel_lanes = max(1, total_rpm // (4 * 10))  # 10 req/min per model per lane
        self.max_parallel_lanes = max_parallel_lanes

        self._semaphore = asyncio.Semaphore(self.max_parallel_lanes)

    async def run_bug(self, bug: SeedBug) -> list[LaneResult]:
        """Run both modes for a single bug."""
        source_repo = self.source_repos.get(bug.repo_url)
        if not source_repo:
            return [
                LaneResult(
                    bug=bug,
                    mode="falsification",
                    success=False,
                    verdict="error",
                    test_passed=None,
                    tokens_total=0,
                    elapsed_s=0.0,
                    error=f"Source repo not found: {bug.repo_url}",
                ),
                LaneResult(
                    bug=bug,
                    mode="flat",
                    success=False,
                    verdict="error",
                    test_passed=None,
                    tokens_total=0,
                    elapsed_s=0.0,
                    error=f"Source repo not found: {bug.repo_url}",
                ),
            ]

        # Create isolated audit DB for this bug
        repo_key = bug.repo_url.replace('/', '_')
        audit_db = self.audit_root / f"audit_{repo_key}_{bug.commit_sha[:8]}.db"
        sink = AuditSink(audit_db)

        # Create worktree manager for this bug
        bases_root = self.bases_root / f"bases_{repo_key}_{bug.commit_sha[:8]}"
        wt_manager = WorktreeManager(source_repo, bases_root)

        results: list[LaneResult] = []

        # Run falsification loop
        falsification_result = await self._run_falsification(
            bug, source_repo, wt_manager, sink
        )
        results.append(falsification_result)

        # Run flat control arm
        flat_result = await self._run_flat(
            bug, source_repo, wt_manager, sink
        )
        results.append(flat_result)

        sink.close()
        return results

    async def _run_falsification(
        self,
        bug: SeedBug,
        source_repo: Path,
        wt_manager: WorktreeManager,
        sink: AuditSink,
    ) -> LaneResult:
        """Run the full falsification loop for a bug."""
        async with self._semaphore:
            start = time.perf_counter()
            try:
                # Build graph with per-lane adapters
                coder = self.coder_adapter_factory()
                adversary = self.adversary_adapter_factory()
                critic = self.critic_adapter_factory()
                arbiter = self.arbiter_adapter_factory()

                app = build_session_graph(
                    adapters={
                        "coder": coder,
                        "adversary": adversary,
                        "critic": critic,
                        "arbiter": arbiter,
                    },
                    sink=sink,
                    worktree_root=wt_manager,
                )

                initial_state = {
                    "task": bug.description,
                    "thread_id": f"fal-{bug.commit_sha[:8]}",
                    "repo_url": bug.repo_url,
                    "license": bug.license,
                    "commit_sha": bug.commit_sha,
                    "bug_type": bug.bug_type,
                }

                final_state = await app.ainvoke(initial_state)
                elapsed = time.perf_counter() - start

                return LaneResult(
                    bug=bug,
                    mode="falsification",
                    success=True,
                    verdict=final_state.get("verdict", "unknown"),
                    test_passed=final_state.get("test_passed"),
                    tokens_total=final_state.get("tokens_total", 0),
                    elapsed_s=elapsed,
                    audit_event_ids=final_state.get("audit_event_ids", []),
                )

            except Exception as exc:  # noqa: BLE001
                elapsed = time.perf_counter() - start
                return LaneResult(
                    bug=bug,
                    mode="falsification",
                    success=False,
                    verdict="error",
                    test_passed=None,
                    tokens_total=0,
                    elapsed_s=elapsed,
                    error=str(exc),
                )

    async def _run_flat(
        self,
        bug: SeedBug,
        source_repo: Path,
        wt_manager: WorktreeManager,
        sink: AuditSink,
    ) -> LaneResult:
        """Run the flat control arm for a bug."""
        async with self._semaphore:
            start = time.perf_counter()
            try:
                coder = self.coder_adapter_factory()
                flat_loop = FlatLoop(
                    coder=coder,
                    sink=sink,
                    source_repo=source_repo,
                    bases_root=wt_manager.bases_root,
                )

                result = flat_loop.run(
                    task=bug.description,
                    repo_url=bug.repo_url,
                    license=bug.license,
                    commit_sha=bug.commit_sha,
                    bug_type=bug.bug_type,
                )
                elapsed = time.perf_counter() - start

                return LaneResult(
                    bug=bug,
                    mode="flat",
                    success=True,
                    verdict=result.verdict,
                    test_passed=result.test_passed,
                    tokens_total=result.tokens_total,
                    elapsed_s=elapsed,
                    audit_event_ids=result.audit_event_ids,
                )

            except Exception as exc:  # noqa: BLE001
                elapsed = time.perf_counter() - start
                return LaneResult(
                    bug=bug,
                    mode="flat",
                    success=False,
                    verdict="error",
                    test_passed=None,
                    tokens_total=0,
                    elapsed_s=elapsed,
                    error=str(exc),
                )

    async def run_corpus(self) -> AblationRun:
        """Run ablation across the entire corpus."""
        run_id = f"ablation-{int(time.time())}"
        all_results: list[LaneResult] = []

        # Create tasks for each bug
        tasks = [self.run_bug(bug) for bug in self.corpus.bugs]

        # Run with asyncio.gather (respects semaphore)
        bug_results = await asyncio.gather(*tasks, return_exceptions=True)

        for i, bug in enumerate(self.corpus.bugs):
            result = bug_results[i]
            if isinstance(result, Exception):
                all_results.append(LaneResult(
                    bug=bug,
                    mode="falsification",
                    success=False,
                    verdict="error",
                    test_passed=None,
                    tokens_total=0,
                    elapsed_s=0.0,
                    error=str(result),
                ))
                all_results.append(LaneResult(
                    bug=bug,
                    mode="flat",
                    success=False,
                    verdict="error",
                    test_passed=None,
                    tokens_total=0,
                    elapsed_s=0.0,
                    error=str(result),
                ))
            else:
                all_results.extend(result)

        return AblationRun(
            run_id=run_id,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            lane_results=all_results,
            config={
                "max_parallel_lanes": self.max_parallel_lanes,
                "total_bugs": len(self.corpus.bugs),
                "pacing_configs": {k: asdict(v) for k, v in self.pacing_configs.items()},
            },
        )

    def save_run(self, run: AblationRun, output_path: Path) -> None:
        """Save ablation run to JSON."""
        output_path.write_text(json.dumps(run.to_dict(), indent=2))
