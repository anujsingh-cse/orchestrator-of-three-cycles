"""Diff apply contract (T6, D8) — CRLF-normalized, check-then-apply, binary-safe.

Failure routes: any rejection here is a ``patch_fix`` verdict — the Coder
retries with the rejection reason as critique context.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

APPLY_REJECTED_BINARY = "rejected_binary"
APPLY_REJECTED_CHECK = "rejected_check"
APPLY_APPLIED = "applied"
APPLY_EMPTY = "empty"


@dataclass
class ApplyResult:
    status: str
    detail: str = ""
    touched: list[str] = field(default_factory=list)


def normalize_lf(patch_text: str) -> str:
    """D8: the repo is LF-only (.gitattributes); CRLF in a patch breaks apply."""
    return patch_text.replace("\r\n", "\n")


def touched_paths(patch_text: str) -> list[str]:
    paths: list[str] = []
    for line in patch_text.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                paths.append(parts[3][2:])
    return paths


def apply_patch(worktree_root: Path, patch_text: str) -> ApplyResult:
    """Apply a unified diff inside the worktree; never outside (D5, D8).

    Order: LF-normalize -> binary scan -> ``git apply --check`` -> apply.
    ``git apply`` with cwd=worktree_root cannot touch files outside it.
    """
    patch = normalize_lf(patch_text).strip()
    if not patch:
        return ApplyResult(APPLY_EMPTY, "empty patch")
    patch = patch + "\n"  # strip() ate the terminator; git apply needs it
    if "\x00" in patch:
        return ApplyResult(APPLY_REJECTED_BINARY, "NUL bytes — binary patch rejected")

    def _git_apply(check_only: bool) -> subprocess.CompletedProcess[bytes]:
        cmd = ["git", "apply", "--whitespace=nowarn", "--check" if check_only else "-"]
        # bytes input: text=True would translate LF->CRLF in the pipe on Windows
        return subprocess.run(
            cmd,
            input=patch.encode("utf-8"),
            capture_output=True,
            cwd=str(worktree_root),
            timeout=60,
        )

    check = _git_apply(check_only=True)
    if check.returncode != 0:
        detail = check.stderr.decode("utf-8", errors="replace").strip()[:500]
        return ApplyResult(APPLY_REJECTED_CHECK, detail)
    applied = _git_apply(check_only=False)
    if applied.returncode != 0:
        detail = applied.stderr.decode("utf-8", errors="replace").strip()[:500]
        return ApplyResult(APPLY_REJECTED_CHECK, detail)
    return ApplyResult(APPLY_APPLIED, touched=touched_paths(patch))
