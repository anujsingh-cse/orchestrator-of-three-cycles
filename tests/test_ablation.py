"""Tests for ablation module (T10, T11, D17, D18)."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import pytest

from orchestrator.ablation.control_arm import FlatLoop, FlatLoopResult
from orchestrator.ablation.corpus import DEFAULT_CORPUS, SeedBug, SeedCorpus
from orchestrator.adapters.llm.fake import FakeAdapter
from orchestrator.audit.sink import AuditSink


@pytest.fixture
def temp_db() -> Path:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        return Path(f.name)


@pytest.fixture
def sink(temp_db: Path) -> AuditSink:
    return AuditSink(temp_db)


@pytest.fixture
def fake_coder() -> FakeAdapter:
    return FakeAdapter(
        model_id="fake-coder",
        script=lambda messages: "diff --git a/foo.py b/foo.py\n- x = 1\n+ x = 2",
    )


def test_flat_loop_basic(sink: AuditSink, fake_coder: FakeAdapter) -> None:
    """Test basic flat loop execution."""
    with tempfile.TemporaryDirectory() as tmpdir:
        source_repo = Path(tmpdir) / "source"
        bases_root = Path(tmpdir) / "bases"
        source_repo.mkdir()
        # Initialize a git repo for worktree
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

        loop = FlatLoop(
            coder=fake_coder,
            sink=sink,
            source_repo=source_repo,
            bases_root=bases_root,
            agent_id="test-flat",
        )

        result = loop.run(
        task="Change x from 1 to 2",
        repo_url="https://github.com/test/repo",
        license="MIT",
        commit_sha="abc123",
        bug_type="logic",
    )

    assert isinstance(result, FlatLoopResult)
    assert result.task == "Change x from 1 to 2"
    assert "x = 2" in result.patch
    assert result.verdict in ("pass", "fail", "error")
    assert result.coder_rounds == 1
    assert result.tokens_total > 0
    assert len(result.audit_event_ids) >= 1


def test_flat_loop_multiple_rounds(sink: AuditSink) -> None:
    """Test flat loop with multiple rounds."""
    responses = [
        "diff --git a/foo.py b/foo.py\n- x = 1\n+ x = 2",  # round 1
        "diff --git a/foo.py b/foo.py\n- x = 2\n+ x = 3",  # round 2
    ]
    call_count = [0]

    def script(messages):
        idx = call_count[0]
        call_count[0] += 1
        return responses[min(idx, len(responses) - 1)]

    fake_coder = FakeAdapter(
        model_id="fake-coder",
        script=script,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        source_repo = Path(tmpdir) / "source"
        bases_root = Path(tmpdir) / "bases"
        source_repo.mkdir()
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

        loop = FlatLoop(
            coder=fake_coder,
            sink=sink,
            source_repo=source_repo,
            bases_root=bases_root,
            agent_id="test-flat",
            max_rounds=2,
        )

        result = loop.run(task="Increment x twice")

    assert result.coder_rounds == 2
    # Should have coder events for both rounds + runner event
    assert len(result.audit_event_ids) >= 3


def test_flat_loop_error_handling(sink: AuditSink) -> None:
    """Test flat loop handles errors gracefully."""
    # Adapter that raises an exception
    class ErrorAdapter(FakeAdapter):
        def _render(self, messages):
            raise RuntimeError("LLM API error")

    with tempfile.TemporaryDirectory() as tmpdir:
        source_repo = Path(tmpdir) / "source"
        bases_root = Path(tmpdir) / "bases"
        source_repo.mkdir()
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

        loop = FlatLoop(
            coder=ErrorAdapter(model_id="error-coder"),
            sink=sink,
            source_repo=source_repo,
            bases_root=bases_root,
            agent_id="test-error",
        )

        result = loop.run(task="This will fail")

    assert result.verdict == "error"
    assert result.error is not None
    assert "LLM API error" in result.error


def test_seed_corpus_basic() -> None:
    """Test seed corpus operations."""
    corpus = SeedCorpus()

    bug = SeedBug(
        repo_url="https://github.com/test/repo",
        commit_sha="abc123",
        fixed_commit_sha="def456",
        bug_type="logic",
        description="Test bug",
        test_file="tests/test_foo.py",
        test_function="test_foo",
        license="MIT",
    )

    corpus.add(bug)

    assert len(corpus.bugs) == 1
    assert corpus.filter_by_type("logic") == [bug]
    assert corpus.filter_by_repo("https://github.com/test/repo") == [bug]


def test_seed_corpus_manifest() -> None:
    """Test corpus manifest generation."""
    corpus = DEFAULT_CORPUS

    manifest = corpus.to_manifest()

    assert manifest["total_bugs"] >= 15
    assert "by_type" in manifest
    assert "by_repo" in manifest
    assert "logic" in manifest["by_type"]
    assert "off-by-one" in manifest["by_type"]
    assert "race" in manifest["by_type"]
    assert "config" in manifest["by_type"]
    assert "rename" in manifest["by_type"]


def test_seed_corpus_bug_types() -> None:
    """Test that corpus has all required bug types."""
    corpus = DEFAULT_CORPUS

    bug_types = corpus.bug_types()

    required_types = {"logic", "off-by-one", "rename", "race", "config"}
    for bt in required_types:
        assert bt in bug_types, f"Missing bug type: {bt}"


def test_seed_corpus_repos() -> None:
    """Test that corpus has at least 3 repos."""
    corpus = DEFAULT_CORPUS

    repos = corpus.repos()
    assert len(repos) >= 3


def test_seed_corpus_save_load_manifest() -> None:
    """Test saving and loading corpus manifest."""
    corpus = DEFAULT_CORPUS

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = Path(f.name)

    try:
        saved_path = corpus.save_manifest(path)
        assert saved_path.exists()

        loaded = SeedCorpus.load_manifest(saved_path)
        assert len(loaded.bugs) == len(corpus.bugs)
        assert loaded.bugs[0].bug_type == corpus.bugs[0].bug_type
    finally:
        path.unlink(missing_ok=True)


def test_default_corpus_size() -> None:
    """Test default corpus meets minimum size (15 bugs, 3 repos)."""
    corpus = DEFAULT_CORPUS

    assert len(corpus.bugs) >= 15
    assert len(corpus.repos()) >= 3

    # Check distribution
    by_type = {bt: len(corpus.filter_by_type(bt)) for bt in corpus.bug_types()}
    for count in by_type.values():
        assert count >= 1  # At least 1 per type


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
