"""Tests for ablation runner and report (T11, D16, D23)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from orchestrator.ablation.corpus import DEFAULT_CORPUS, SeedBug, SeedCorpus
from orchestrator.ablation.report import (
    AblationReport,
    cohens_d,
    compute_effect_sizes,
    compute_mode_stats,
    generate_report,
    interpret_cohens_d,
    wilson_ci,
)
from orchestrator.ablation.scheduler import AblationRun, AblationScheduler, LaneResult
from orchestrator.adapters.llm.pacing import PacingConfig


class TestWilsonCI:
    """Tests for Wilson confidence interval."""

    def test_wilson_ci_zero(self) -> None:
        low, high = wilson_ci(0.0, 10)
        assert low == 0.0
        assert high > 0.0

    def test_wilson_ci_one(self) -> None:
        low, high = wilson_ci(1.0, 10)
        assert low < 1.0
        assert high == 1.0

    def test_wilson_ci_middle(self) -> None:
        low, high = wilson_ci(0.5, 100)
        assert 0.4 < low < 0.5
        assert 0.5 < high < 0.6

    def test_wilson_ci_n_zero(self) -> None:
        low, high = wilson_ci(0.5, 0)
        assert low == 0.0
        assert high == 1.0


class TestCohensD:
    """Tests for Cohen's d effect size."""

    def test_cohens_d_zero(self) -> None:
        d = cohens_d(0.5, 0.5, 0.1, 0.1, 10, 10)
        assert d == 0.0

    def test_cohens_d_positive(self) -> None:
        d = cohens_d(0.7, 0.5, 0.1, 0.1, 10, 10)
        assert d > 0

    def test_cohens_d_negative(self) -> None:
        d = cohens_d(0.5, 0.7, 0.1, 0.1, 10, 10)
        assert d < 0

    def test_cohens_d_small_n(self) -> None:
        d = cohens_d(0.7, 0.5, 0.1, 0.1, 1, 10)
        assert d == 0.0  # n <= 1 returns 0


class TestInterpretCohensD:
    """Tests for Cohen's d interpretation."""

    def test_negligible(self) -> None:
        assert interpret_cohens_d(0.1) == "negligible"
        assert interpret_cohens_d(-0.1) == "negligible"

    def test_small(self) -> None:
        assert interpret_cohens_d(0.3) == "small"
        assert interpret_cohens_d(-0.3) == "small"

    def test_medium(self) -> None:
        assert interpret_cohens_d(0.6) == "medium"
        assert interpret_cohens_d(-0.6) == "medium"

    def test_large(self) -> None:
        assert interpret_cohens_d(1.0) == "large"
        assert interpret_cohens_d(-1.0) == "large"


class TestComputeModeStats:
    """Tests for mode statistics computation."""

    def make_result(
        self,
        mode: str,
        bug_type: str,
        repo: str,
        test_passed: bool = True,
        success: bool = True,
    ) -> LaneResult:
        bug = SeedBug(
            repo_url=repo,
            commit_sha="abc123",
            fixed_commit_sha="def456",
            bug_type=bug_type,
            description="Test bug",
            test_file="test.py",
            test_function="test_func",
            license="MIT",
        )
        return LaneResult(
            bug=bug,
            mode=mode,
            success=success,
            verdict="pass" if test_passed else "fail",
            test_passed=test_passed,
            tokens_total=100,
            elapsed_s=1.0,
            audit_event_ids=[],
        )

    def test_compute_mode_stats_empty(self) -> None:
        stats = compute_mode_stats([], "falsification")
        assert stats.n == 0
        assert stats.pass_rate == 0.0

    def test_compute_mode_stats_pass_rate(self) -> None:
        results = [
            self.make_result("falsification", "logic", "repo1", test_passed=True),
            self.make_result("falsification", "logic", "repo1", test_passed=True),
            self.make_result("falsification", "off-by-one", "repo2", test_passed=False),
        ]
        stats = compute_mode_stats(results, "falsification")

        assert stats.n == 3
        assert stats.n_pass == 2
        assert stats.pass_rate == 2 / 3
        assert 0.0 < stats.ci_95_low < stats.pass_rate < stats.ci_95_high < 1.0

    def test_compute_mode_stats_by_bug_type(self) -> None:
        results = [
            self.make_result("falsification", "logic", "repo1", test_passed=True),
            self.make_result("falsification", "logic", "repo1", test_passed=False),
            self.make_result("falsification", "off-by-one", "repo2", test_passed=True),
        ]
        stats = compute_mode_stats(results, "falsification")

        assert stats.by_bug_type["logic"]["total"] == 2
        assert stats.by_bug_type["logic"]["passed"] == 1
        assert stats.by_bug_type["off-by-one"]["total"] == 1
        assert stats.by_bug_type["off-by-one"]["passed"] == 1

    def test_compute_mode_stats_by_repo(self) -> None:
        results = [
            self.make_result("falsification", "logic", "repo1", test_passed=True),
            self.make_result("falsification", "off-by-one", "repo1", test_passed=False),
            self.make_result("falsification", "race", "repo2", test_passed=True),
        ]
        stats = compute_mode_stats(results, "falsification")

        assert stats.by_repo["repo1"]["total"] == 2
        assert stats.by_repo["repo1"]["passed"] == 1
        assert stats.by_repo["repo2"]["total"] == 1
        assert stats.by_repo["repo2"]["passed"] == 1


class TestComputeEffectSizes:
    """Tests for effect size computation."""

    def make_result(
        self,
        mode: str,
        test_passed: bool = True,
        tokens: int = 100,
        elapsed: float = 1.0,
    ) -> LaneResult:
        bug = SeedBug(
            repo_url="https://github.com/test/repo",
            commit_sha="abc123",
            fixed_commit_sha="def456",
            bug_type="logic",
            description="Test bug",
            test_file="test.py",
            test_function="test_func",
            license="MIT",
        )
        return LaneResult(
            bug=bug,
            mode=mode,
            success=True,
            verdict="pass" if test_passed else "fail",
            test_passed=test_passed,
            tokens_total=tokens,
            elapsed_s=elapsed,
            audit_event_ids=[],
        )

    def make_result_varied(
        self,
        mode: str,
        tokens: int = 100,
        elapsed: float = 1.0,
    ) -> LaneResult:
        """Make result with varied tokens/elapsed for effect size testing."""
        bug = SeedBug(
            repo_url="https://github.com/test/repo",
            commit_sha="abc123",
            fixed_commit_sha="def456",
            bug_type="logic",
            description="Test bug",
            test_file="test.py",
            test_function="test_func",
            license="MIT",
        )
        return LaneResult(
            bug=bug,
            mode=mode,
            success=True,
            verdict="pass",
            test_passed=True,
            tokens_total=tokens,
            elapsed_s=elapsed,
            audit_event_ids=[],
        )

    def test_compute_effect_sizes_pass_rate(self) -> None:
        fal_results = [
            self.make_result("falsification", test_passed=True),
            self.make_result("falsification", test_passed=True),
            self.make_result("falsification", test_passed=False),
        ]
        fl_results = [
            self.make_result("flat", test_passed=False),
            self.make_result("flat", test_passed=False),
            self.make_result("flat", test_passed=False),
        ]

        effects = compute_effect_sizes(fal_results, fl_results)

        # Should have pass_rate effect
        pass_rate_effects = [e for e in effects if e.metric == "pass_rate"]
        assert len(pass_rate_effects) == 1
        effect = pass_rate_effects[0]
        assert effect.cohen_d > 0  # falsification better
        assert effect.interpretation in ("small", "medium", "large")

    def test_compute_effect_sizes_tokens(self) -> None:
        # Need variance for Cohen's d, so use varied tokens
        fal_results = [
            self.make_result_varied("falsification", tokens=200),
            self.make_result_varied("falsification", tokens=210),
            self.make_result_varied("falsification", tokens=190),
        ]
        fl_results = [
            self.make_result_varied("flat", tokens=100),
            self.make_result_varied("flat", tokens=110),
            self.make_result_varied("flat", tokens=90),
        ]

        effects = compute_effect_sizes(fal_results, fl_results)

        token_effects = [e for e in effects if e.metric == "tokens_total"]
        assert len(token_effects) == 1
        effect = token_effects[0]
        assert effect.cohen_d > 0

    def test_compute_effect_sizes_time(self) -> None:
        # Need variance for Cohen's d
        fal_results = [
            self.make_result_varied("falsification", elapsed=2.0),
            self.make_result_varied("falsification", elapsed=2.2),
            self.make_result_varied("falsification", elapsed=1.8),
        ]
        fl_results = [
            self.make_result_varied("flat", elapsed=1.0),
            self.make_result_varied("flat", elapsed=1.1),
            self.make_result_varied("flat", elapsed=0.9),
        ]

        effects = compute_effect_sizes(fal_results, fl_results)

        time_effects = [e for e in effects if e.metric == "elapsed_s"]
        assert len(time_effects) == 1
        effect = time_effects[0]
        assert effect.cohen_d > 0


class TestGenerateReport:
    """Tests for report generation."""

    def make_fake_run(self) -> AblationRun:
        bug = SeedBug(
            repo_url="https://github.com/test/repo",
            commit_sha="abc123",
            fixed_commit_sha="def456",
            bug_type="logic",
            description="Test bug",
            test_file="test.py",
            test_function="test_func",
            license="MIT",
        )

        return AblationRun(
            run_id="test-run",
            timestamp="2026-08-19T12:00:00Z",
            lane_results=[
                LaneResult(
                    bug=bug,
                    mode="falsification",
                    success=True,
                    verdict="pass",
                    test_passed=True,
                    tokens_total=100,
                    elapsed_s=1.0,
                    audit_event_ids=[],
                ),
                LaneResult(
                    bug=bug,
                    mode="flat",
                    success=True,
                    verdict="fail",
                    test_passed=False,
                    tokens_total=150,
                    elapsed_s=1.5,
                    audit_event_ids=[],
                ),
            ],
            config={"max_parallel_lanes": 2},
        )

    def test_generate_report(self) -> None:
        run = self.make_fake_run()
        report = generate_report(run, DEFAULT_CORPUS)

        assert isinstance(report, AblationReport)
        assert report.run_id == "test-run"
        assert report.total_bugs == len(DEFAULT_CORPUS.bugs)
        assert "falsification" in report.mode_stats
        assert "flat" in report.mode_stats
        assert len(report.effect_sizes) >= 1

    def test_report_to_markdown(self) -> None:
        run = self.make_fake_run()
        report = generate_report(run, DEFAULT_CORPUS)
        md = report.to_markdown()

        assert "# Ablation Report" in md
        assert "Mode Comparison" in md
        assert "Effect Sizes" in md
        assert "Bug-Type Distribution" in md
        assert "Repo Distribution" in md
        assert "Configuration" in md

    def test_report_to_dict(self) -> None:
        run = self.make_fake_run()
        report = generate_report(run, DEFAULT_CORPUS)
        d = report.to_dict()

        assert d["run_id"] == "test-run"
        assert "mode_stats" in d
        assert "effect_sizes" in d
        assert "bug_type_distribution" in d


class TestAblationScheduler:
    """Tests for ablation scheduler (mocked)."""

    @pytest.mark.asyncio
    async def test_scheduler_initialization(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            source_repo = tmp / "source"
            source_repo.mkdir()

            # Init git repo
            import subprocess
            subprocess.run(
                ["git", "init"], cwd=source_repo, check=True, capture_output=True
            )
            subprocess.run(
                ["git", "config", "user.email", "test@test.com"], cwd=source_repo, check=True
            )
            subprocess.run(
                ["git", "config", "user.name", "Test"], cwd=source_repo, check=True
            )
            (source_repo / "foo.py").write_text("x = 1\n")
            subprocess.run(["git", "add", "."], cwd=source_repo, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=source_repo, check=True)

            corpus = SeedCorpus(bugs=[
                SeedBug(
                    repo_url="https://github.com/test/repo",
                    commit_sha="abc123",
                    fixed_commit_sha="def456",
                    bug_type="logic",
                    description="Test bug",
                    test_file="test.py",
                    test_function="test_func",
                    license="MIT",
                ),
            ])

            scheduler = AblationScheduler(
                corpus=corpus,
                source_repos={"https://github.com/test/repo": source_repo},
                bases_root=tmp / "bases",
                audit_root=tmp / "audit",
                coder_adapter_factory=lambda: MagicMock(),
                adversary_adapter_factory=lambda: MagicMock(),
                critic_adapter_factory=lambda: MagicMock(),
                arbiter_adapter_factory=lambda: MagicMock(),
                pacing_configs={
                    "coder": PacingConfig(max_requests_per_minute=60),
                    "adversary": PacingConfig(max_requests_per_minute=20),
                    "critic": PacingConfig(max_requests_per_minute=30),
                    "arbiter": PacingConfig(max_requests_per_minute=10),
                },
            )

            assert scheduler.max_parallel_lanes >= 1

    def test_save_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "run.json"
            run = AblationRun(
                run_id="test-run",
                timestamp="2026-08-19T12:00:00Z",
                lane_results=[],
                config={},
            )

            # Need to call the save_run method on a scheduler instance
            # Just test the JSON serialization
            import json
            output_path.write_text(json.dumps(run.to_dict(), indent=2))
            assert output_path.exists()

            # Verify it can be read back
            loaded = json.loads(output_path.read_text())
            assert loaded["run_id"] == "test-run"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
