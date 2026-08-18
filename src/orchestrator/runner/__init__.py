"""Runner package (T5/T6) — worktree isolation, process control, diff contract."""

from orchestrator.runner.diff import (
    APPLY_APPLIED,
    APPLY_EMPTY,
    APPLY_REJECTED_BINARY,
    APPLY_REJECTED_CHECK,
    ApplyResult,
    apply_patch,
    normalize_lf,
)
from orchestrator.runner.process import SCRUB_KEYS, TestResult, run_tests, scrub_env
from orchestrator.runner.worktree import (
    WorktreeError,
    create_worktree,
    is_inside_worktree,
    remove_worktree,
)

__all__ = [
    "APPLY_APPLIED",
    "APPLY_EMPTY",
    "APPLY_REJECTED_BINARY",
    "APPLY_REJECTED_CHECK",
    "ApplyResult",
    "SCRUB_KEYS",
    "TestResult",
    "WorktreeError",
    "apply_patch",
    "create_worktree",
    "is_inside_worktree",
    "normalize_lf",
    "remove_worktree",
    "run_tests",
    "scrub_env",
]
