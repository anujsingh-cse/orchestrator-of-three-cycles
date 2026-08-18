"""T6 diff apply contract tests — CRLF, binary, rejection routing (D8)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from orchestrator.runner.diff import (
    APPLY_APPLIED,
    APPLY_EMPTY,
    APPLY_REJECTED_BINARY,
    APPLY_REJECTED_CHECK,
    apply_patch,
    normalize_lf,
)


def _make_patch(repo: Path, filename: str, old: str, new: str) -> str:
    """Generate a real unified diff for a tracked file via git."""
    (repo / filename).write_text(old, encoding="utf-8", newline="\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
    (repo / filename).write_text(new, encoding="utf-8", newline="\n")
    diff = subprocess.run(
        ["git", "-C", str(repo), "diff"], capture_output=True, text=True, check=True
    ).stdout
    subprocess.run(["git", "-C", str(repo), "checkout", "--", filename], check=True)
    return diff


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True)
    (root / ".gitattributes").write_text("* text eol=lf\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "attributes"], check=True)


class TestCRLFContract:
    def test_lf_patch_applies_over_crlf_file(self, tmp_path: Path) -> None:
        _init_repo(tmp_path)
        old = "VALUE = 1\n"
        new = "VALUE = 2\n"
        patch = _make_patch(tmp_path, "main.py", old, new)
        (tmp_path / "main.py").write_bytes(b"VALUE = 1\r\n")  # CRLF on disk
        result = apply_patch(tmp_path, patch)
        assert result.status == APPLY_APPLIED
        assert (tmp_path / "main.py").read_bytes() == b"VALUE = 2\n"  # LF-normalized repo file

    def test_crlf_patch_normalized(self) -> None:
        assert normalize_lf("a\r\nb\r\n") == "a\nb\n"

    def test_touched_paths_recorded(self, tmp_path: Path) -> None:
        _init_repo(tmp_path)
        patch = _make_patch(tmp_path, "main.py", "VALUE = 1\n", "VALUE = 3\n")
        result = apply_patch(tmp_path, patch)
        assert result.touched == ["main.py"]


class TestRejections:
    def test_empty_patch(self, tmp_path: Path) -> None:
        result = apply_patch(tmp_path, "   \n")
        assert result.status == APPLY_EMPTY

    def test_binary_patch_rejected(self, tmp_path: Path) -> None:
        result = apply_patch(tmp_path, "diff --git a/x.bin b/x.bin\n\x00\x01\x02binary")
        assert result.status == APPLY_REJECTED_BINARY

    def test_apply_check_failure_rejected(self, tmp_path: Path) -> None:
        _init_repo(tmp_path)
        patch = (
            "diff --git a/missing.py b/missing.py\n--- a/missing.py\n+++ b/missing.py\n"
            "@@ -1,1 +1,2 @@\n+new\n"
        )
        result = apply_patch(tmp_path, patch)
        assert result.status == APPLY_REJECTED_CHECK
        assert result.detail  # stderr captured

    def test_rejected_patch_does_not_touch_disk(self, tmp_path: Path) -> None:
        _init_repo(tmp_path)
        (tmp_path / "main.py").write_text("KEEP = 1\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "b"], check=True)
        patch = (
            "diff --git a/main.py b/main.py\n--- a/main.py\n+++ b/main.py\n"
            "@@ -1,1 +1,1 @@\n-KEEP = 1\n+DIFFERENT = 2\n@@ -99,1 +1,1 @@\n+noise\n"
        )
        result = apply_patch(tmp_path, patch)
        assert result.status == APPLY_REJECTED_CHECK
        assert "KEEP = 1" in (tmp_path / "main.py").read_text(encoding="utf-8")


class TestRoutingSemantics:
    """A rejected apply is 'patch_fix' semantics — coder retries (T6 verify)."""

    def test_rejection_statuses_map_to_patch_fix_route(self, tmp_path: Path) -> None:
        from orchestrator.graph.builder import route_from_runner
        from orchestrator.graph.state import SessionState

        _init_repo(tmp_path)
        result = apply_patch(tmp_path, "diff --git a/ghost.py b/ghost.py\n")
        assert result.status == APPLY_REJECTED_CHECK
        state: SessionState = {"test_passed": False, "coder_rounds": 1, "thread_id": "t"}
        assert route_from_runner(state) == "coder"
