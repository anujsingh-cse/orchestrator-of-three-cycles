"""Ablation report — statistical framing (T11, D23).

Produces evidence report with:
- n (sample size per mode)
- Effect size (Cohen's d for pass rate difference)
- Variance (confidence intervals)
- Bug-type distribution
- Per-repo breakdown
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orchestrator.ablation.corpus import SeedCorpus
from orchestrator.ablation.scheduler import AblationRun, LaneResult


@dataclass(frozen=True)
class ModeStats:
    """Statistics for one mode (falsification or flat)."""

    mode: str
    n: int
    n_pass: int
    pass_rate: float
    ci_95_low: float
    ci_95_high: float
    mean_tokens: float
    mean_elapsed_s: float
    by_bug_type: dict[str, dict[str, int]]
    by_repo: dict[str, dict[str, int]]


@dataclass(frozen=True)
class EffectSize:
    """Effect size between two modes."""

    metric: str
    falsification_mean: float
    flat_mean: float
    cohen_d: float
    interpretation: str  # "small", "medium", "large"


@dataclass(frozen=True)
class AblationReport:
    """Complete ablation report (D23)."""

    run_id: str
    timestamp: str
    mode_stats: dict[str, ModeStats]
    effect_sizes: list[EffectSize]
    bug_type_distribution: dict[str, int]
    repo_distribution: dict[str, int]
    total_bugs: int
    config: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "mode_stats": {k: v.__dict__ for k, v in self.mode_stats.items()},
            "effect_sizes": [v.__dict__ for v in self.effect_sizes],
            "bug_type_distribution": self.bug_type_distribution,
            "repo_distribution": self.repo_distribution,
            "total_bugs": self.total_bugs,
            "config": self.config,
        }

    def to_markdown(self) -> str:
        """Generate human-readable markdown report."""
        lines = [
            f"# Ablation Report: {self.run_id}",
            f"**Timestamp:** {self.timestamp}",
            f"**Total bugs in corpus:** {self.total_bugs}",
            "",
            "## Mode Comparison",
            "",
        ]

        # Table of mode stats
        lines.append("| Mode | n | Pass Rate | 95% CI | Mean Tokens | Mean Time (s) |")
        lines.append("|------|---|-----------|--------|-------------|---------------|")

        for mode, stats in self.mode_stats.items():
            lines.append(
                f"| {mode} | {stats.n} | {stats.pass_rate:.1%} | "
                f"[{stats.ci_95_low:.1%}, {stats.ci_95_high:.1%}] | "
                f"{stats.mean_tokens:.0f} | {stats.mean_elapsed_s:.1f} |"
            )

        lines.append("")
        lines.append("## Effect Sizes")
        lines.append("")
        lines.append("| Metric | Falsification | Flat | Cohen's d | Interpretation |")
        lines.append("|--------|---------------|------|-----------|----------------|")

        for es in self.effect_sizes:
            lines.append(
                f"| {es.metric} | {es.falsification_mean:.2f} | {es.flat_mean:.2f} | "
                f"{es.cohen_d:.2f} | {es.interpretation} |"
            )

        lines.append("")
        lines.append("## Bug-Type Distribution")
        lines.append("")
        for bt, count in sorted(self.bug_type_distribution.items()):
            lines.append(f"- **{bt}**: {count}")

        lines.append("")
        lines.append("## Repo Distribution")
        lines.append("")
        for repo, count in sorted(self.repo_distribution.items()):
            lines.append(f"- **{repo}**: {count}")

        lines.append("")
        lines.append("## Configuration")
        lines.append("```json")
        import json
        lines.append(json.dumps(self.config, indent=2))
        lines.append("```")

        return "\n".join(lines)


def wilson_ci(pass_rate: float, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for binomial proportion."""
    if n == 0:
        return (0.0, 1.0)
    denominator = 1 + z**2 / n
    centre = (pass_rate + z**2 / (2 * n)) / denominator
    half = z * math.sqrt(pass_rate * (1 - pass_rate) / n + z**2 / (4 * n**2)) / denominator
    return (max(0.0, centre - half), min(1.0, centre + half))


def cohens_d(mean1: float, mean2: float, std1: float, std2: float, n1: int, n2: int) -> float:
    """Cohen's d for two independent samples."""
    if n1 <= 1 or n2 <= 1:
        return 0.0
    pooled_std = math.sqrt(((n1 - 1) * std1**2 + (n2 - 1) * std2**2) / (n1 + n2 - 2))
    if pooled_std == 0:
        return 0.0
    return (mean1 - mean2) / pooled_std


def interpret_cohens_d(d: float) -> str:
    """Interpret Cohen's d magnitude."""
    abs_d = abs(d)
    if abs_d < 0.2:
        return "negligible"
    elif abs_d < 0.5:
        return "small"
    elif abs_d < 0.8:
        return "medium"
    else:
        return "large"


def compute_mode_stats(results: list[LaneResult], mode: str) -> ModeStats:
    """Compute statistics for a single mode."""
    mode_results = [r for r in results if r.mode == mode]
    n = len(mode_results)

    if n == 0:
        return ModeStats(
            mode=mode,
            n=0,
            n_pass=0,
            pass_rate=0.0,
            ci_95_low=0.0,
            ci_95_high=0.0,
            mean_tokens=0.0,
            mean_elapsed_s=0.0,
            by_bug_type={},
            by_repo={},
        )

    n_pass = sum(1 for r in mode_results if r.test_passed)
    pass_rate = n_pass / n
    ci_low, ci_high = wilson_ci(pass_rate, n)

    # Mean tokens and elapsed (only successful runs)
    successful = [r for r in mode_results if r.success]
    mean_tokens = sum(r.tokens_total for r in successful) / len(successful) if successful else 0.0
    mean_elapsed = sum(r.elapsed_s for r in successful) / len(successful) if successful else 0.0

    # By bug type
    by_bug_type: dict[str, dict[str, int]] = {}
    for r in mode_results:
        bt = r.bug.bug_type
        by_bug_type.setdefault(bt, {"total": 0, "passed": 0})
        by_bug_type[bt]["total"] += 1
        if r.test_passed:
            by_bug_type[bt]["passed"] += 1

    # By repo
    by_repo: dict[str, dict[str, int]] = {}
    for r in mode_results:
        repo = r.bug.repo_url
        by_repo.setdefault(repo, {"total": 0, "passed": 0})
        by_repo[repo]["total"] += 1
        if r.test_passed:
            by_repo[repo]["passed"] += 1

    return ModeStats(
        mode=mode,
        n=n,
        n_pass=n_pass,
        pass_rate=pass_rate,
        ci_95_low=ci_low,
        ci_95_high=ci_high,
        mean_tokens=mean_tokens,
        mean_elapsed_s=mean_elapsed,
        by_bug_type=by_bug_type,
        by_repo=by_repo,
    )


def compute_effect_sizes(
    falsification_results: list[LaneResult],
    flat_results: list[LaneResult],
) -> list[EffectSize]:
    """Compute effect sizes between falsification and flat modes."""
    effects: list[EffectSize] = []

    # Pass rate effect size
    fal_pass = [1 if r.test_passed else 0 for r in falsification_results if r.success]
    fl_pass = [1 if r.test_passed else 0 for r in flat_results if r.success]

    if fal_pass and fl_pass:
        mean_fal = sum(fal_pass) / len(fal_pass)
        mean_fl = sum(fl_pass) / len(fl_pass)
        std_fal = math.sqrt(sum((x - mean_fal)**2 for x in fal_pass) / max(1, len(fal_pass) - 1))
        std_fl = math.sqrt(sum((x - mean_fl)**2 for x in fl_pass) / max(1, len(fl_pass) - 1))
        d = cohens_d(mean_fal, mean_fl, std_fal, std_fl, len(fal_pass), len(fl_pass))
        effects.append(EffectSize(
            metric="pass_rate",
            falsification_mean=mean_fal,
            flat_mean=mean_fl,
            cohen_d=d,
            interpretation=interpret_cohens_d(d),
        ))

    # Tokens effect size
    fal_tokens = [r.tokens_total for r in falsification_results if r.success]
    fl_tokens = [r.tokens_total for r in flat_results if r.success]

    if fal_tokens and fl_tokens:
        mean_fal = sum(fal_tokens) / len(fal_tokens)
        mean_fl = sum(fl_tokens) / len(fl_tokens)
        variance_fal = sum((x - mean_fal) ** 2 for x in fal_tokens) / max(1, len(fal_tokens) - 1)
        variance_fl = sum((x - mean_fl) ** 2 for x in fl_tokens) / max(1, len(fl_tokens) - 1)
        std_fal = math.sqrt(variance_fal)
        std_fl = math.sqrt(variance_fl)
        d = cohens_d(mean_fal, mean_fl, std_fal, std_fl, len(fal_tokens), len(fl_tokens))
        effects.append(EffectSize(
            metric="tokens_total",
            falsification_mean=mean_fal,
            flat_mean=mean_fl,
            cohen_d=d,
            interpretation=interpret_cohens_d(d),
        ))

    # Elapsed time effect size
    fal_time = [r.elapsed_s for r in falsification_results if r.success]
    fl_time = [r.elapsed_s for r in flat_results if r.success]

    if fal_time and fl_time:
        mean_fal = sum(fal_time) / len(fal_time)
        mean_fl = sum(fl_time) / len(fl_time)
        std_fal = math.sqrt(sum((x - mean_fal)**2 for x in fal_time) / max(1, len(fal_time) - 1))
        std_fl = math.sqrt(sum((x - mean_fl)**2 for x in fl_time) / max(1, len(fl_time) - 1))
        d = cohens_d(mean_fal, mean_fl, std_fal, std_fl, len(fal_time), len(fl_time))
        effects.append(EffectSize(
            metric="elapsed_s",
            falsification_mean=mean_fal,
            flat_mean=mean_fl,
            cohen_d=d,
            interpretation=interpret_cohens_d(d),
        ))

    return effects


def generate_report(run: AblationRun, corpus: SeedCorpus) -> AblationReport:
    """Generate full ablation report from run results."""
    results = run.lane_results

    fal_results = [r for r in results if r.mode == "falsification"]
    fl_results = [r for r in results if r.mode == "flat"]

    mode_stats = {
        "falsification": compute_mode_stats(results, "falsification"),
        "flat": compute_mode_stats(results, "flat"),
    }

    effect_sizes = compute_effect_sizes(fal_results, fl_results)

    # Bug type distribution
    bug_types: dict[str, int] = {}
    for bug in corpus.bugs:
        bug_types[bug.bug_type] = bug_types.get(bug.bug_type, 0) + 1

    # Repo distribution
    repos: dict[str, int] = {}
    for bug in corpus.bugs:
        repos[bug.repo_url] = repos.get(bug.repo_url, 0) + 1

    return AblationReport(
        run_id=run.run_id,
        timestamp=run.timestamp,
        mode_stats=mode_stats,
        effect_sizes=effect_sizes,
        bug_type_distribution=bug_types,
        repo_distribution=repos,
        total_bugs=len(corpus.bugs),
        config=run.config,
    )


def save_report(report: AblationReport, output_path: Path) -> None:
    """Save report to JSON and Markdown."""
    output_path.write_text(json.dumps(report.to_dict(), indent=2))
    md_path = output_path.with_suffix(".md")
    md_path.write_text(report.to_markdown())
