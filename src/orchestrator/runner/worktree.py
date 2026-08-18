"""Worktree isolation (T5, D5) — disjoint roots, never the source repo.

The adversarial patch executes in a throwaway git worktree under a dedicated
data root; the audit DB and source repo live OUTSIDE that root, so the
containment check ``is_inside_worktree`` keeps every runner touchpoint inside.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class WorktreeError(RuntimeError):
    pass


def is_inside_worktree(path: Path, worktree_root: Path) -> bool:
    """Containment check (T5 verify: write-outside-worktree denied)."""
    return path.resolve().is_relative_to(worktree_root.resolve())


def create_worktree(source_repo: Path, bases_root: Path, thread_id: str) -> Path:
    """``git worktree add --detach`` a fresh copy of the source repo HEAD."""
    bases_root.mkdir(parents=True, exist_ok=True)
    target = bases_root / thread_id
    if target.exists():
        shutil.rmtree(target)
    proc = subprocess.run(
        ["git", "-C", str(source_repo), "worktree", "add", "--detach", str(target), "HEAD"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        raise WorktreeError(proc.stderr.strip())
    return target


def remove_worktree(source_repo: Path, worktree_root: Path) -> None:
    subprocess.run(
        ["git", "-C", str(source_repo), "worktree", "remove", "--force", str(worktree_root)],
        capture_output=True,
        text=True,
        timeout=120,
    )
