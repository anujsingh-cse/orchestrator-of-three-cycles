"""T5 runner isolation tests — containment, env scrub, timeout kill, worktrees."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from orchestrator.runner.process import run_tests, scrub_env
from orchestrator.runner.worktree import create_worktree, is_inside_worktree


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True)
    (root / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "init"], check=True)


class TestContainment:
    def test_inside_worktree_allowed(self, tmp_path: Path) -> None:
        root = tmp_path / "wt"
        root.mkdir()
        assert is_inside_worktree(root / "src" / "x.py", root)

    def test_outside_worktree_denied(self, tmp_path: Path) -> None:
        root = tmp_path / "wt"
        outside = tmp_path / "audit.db"
        root.mkdir()
        assert not is_inside_worktree(outside, root)


class TestEnvScrub:
    def test_secrets_removed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NIM_API_KEY", "nvapi-secret")
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA")
        env = scrub_env()
        assert "NIM_API_KEY" not in env
        assert "AWS_ACCESS_KEY_ID" not in env

    def test_benign_vars_preserved(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PATH", "C:\\bin")
        env = scrub_env()
        assert env["PATH"] == "C:\\bin"


class TestWorktree:
    def test_create_and_remove(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        bases = tmp_path / "worktrees"
        wt = create_worktree(repo, bases, "thread-1")
        assert wt.is_dir()
        assert (wt / "main.py").read_text(encoding="utf-8") == "VALUE = 1\n"
        assert is_inside_worktree(wt, bases)
        from orchestrator.runner.worktree import remove_worktree

        remove_worktree(repo, wt)
        assert not wt.exists()


class TestTimeoutKill:
    def test_hung_child_killed(self, tmp_path: Path) -> None:
        result = run_tests(
            tmp_path,
            timeout_s=1.0,
            command=[sys.executable, "-c", "import time; time.sleep(30)"],
        )
        assert not result.passed
        assert result.returncode == -9
        assert "timed out" in result.output
        assert result.duration_s < 10

    def test_fast_child_succeeds(self, tmp_path: Path) -> None:
        result = run_tests(
            tmp_path,
            timeout_s=10.0,
            command=[sys.executable, "-c", "import sys; sys.exit(0)"],
        )
        assert result.passed
        assert result.returncode == 0
