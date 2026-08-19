"""Ablation module — control arm, seed corpus, runner, and report (T10, T11, D17, D18, D16, D23)."""

from orchestrator.ablation.control_arm import FlatLoop, FlatLoopResult
from orchestrator.ablation.corpus import SeedBug, SeedCorpus
from orchestrator.ablation.report import AblationReport, generate_report, save_report
from orchestrator.ablation.scheduler import AblationRun, AblationScheduler, LaneResult

__all__ = [
    "FlatLoop",
    "FlatLoopResult",
    "SeedCorpus",
    "SeedBug",
    "AblationScheduler",
    "LaneResult",
    "AblationRun",
    "AblationReport",
    "generate_report",
    "save_report",
]
