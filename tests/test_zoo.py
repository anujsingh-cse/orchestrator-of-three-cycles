"""Tests for failure zoo (T9, D9, D19)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from orchestrator.audit.event import AuditEvent
from orchestrator.audit.sink import AuditSink
from orchestrator.zoo.curation import CurationPolicy, LicenseClassifier
from orchestrator.zoo.view import ZooSpecimen, ZooView


@pytest.fixture
def temp_db() -> Path:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        return Path(f.name)


@pytest.fixture
def sink(temp_db: Path) -> AuditSink:
    return AuditSink(temp_db)


@pytest.fixture
def sample_events() -> list[AuditEvent]:
    """Create a sample session of audit events."""
    from datetime import datetime

    base_time = datetime(2026, 8, 19, 12, 0, 0)

    provenance = {
        "repo_url": "https://github.com/example/repo",
        "license": "MIT",
        "commit_sha": "abc123",
        "bug_type": "off-by-one",
    }

    events = [
        # Root event (coder)
        AuditEvent(
            id="evt-root-1",
            parent_event_id=None,
            thread_id="thread-1",
            node="coder",
            agent_id="coder-1",
            model_id="nvidia/nemotron-3-super-120b-a12b",
            ts=base_time,
            input_hash="hash1",
            output_hash="hash2",
            payload_in={"task": "Fix off-by-one in loop", **provenance},
            payload_out={
                "patch": (
                    "diff --git a/foo.py b/foo.py\n"
                    "- for i in range(len(items)):\n"
                    "+ for i in range(len(items) - 1):"
                )
            },
            tool_calls=[],
            tokens_in=100,
            tokens_out=50,
        ),
        # Adversary
        AuditEvent(
            id="evt-adv-1",
            parent_event_id="evt-root-1",
            thread_id="thread-1",
            node="adversary",
            agent_id="adversary-1",
            model_id="z-ai/glm-5.2",
            ts=base_time,
            input_hash="hash3",
            output_hash="hash4",
            payload_in={"patch": "diff...", **provenance},
            payload_out={"attack": "Empty list causes index error"},
            tool_calls=[],
            tokens_in=80,
            tokens_out=40,
        ),
        # Critic
        AuditEvent(
            id="evt-crit-1",
            parent_event_id="evt-adv-1",
            thread_id="thread-1",
            node="critic",
            agent_id="critic-1",
            model_id="nvidia/nemotron-3-ultra-550b-a55b",
            ts=base_time,
            input_hash="hash5",
            output_hash="hash6",
            payload_in={
                "task": "Fix off-by-one",
                "patch": "diff...",
                "attack": "Empty list...",
                **provenance,
            },
            payload_out={"critique": "Patch fixes off-by-one but misses empty list case"},
            tool_calls=[],
            tokens_in=120,
            tokens_out=60,
        ),
        # Arbiter
        AuditEvent(
            id="evt-arb-1",
            parent_event_id="evt-crit-1",
            thread_id="thread-1",
            node="arbiter",
            agent_id="arbiter-1",
            model_id="nvidia/nemotron-3-ultra-550b-a55b",
            ts=base_time,
            input_hash="hash7",
            output_hash="hash8",
            payload_in={"patch": "diff...", "attack": "Empty list...", "rounds": 1, **provenance},
            payload_out={"verdict": "patch_fix", "escalated": False},
            tool_calls=[],
            tokens_in=100,
            tokens_out=30,
        ),
        # Runner
        AuditEvent(
            id="evt-run-1",
            parent_event_id="evt-arb-1",
            thread_id="thread-1",
            node="runner",
            agent_id="runner-1",
            model_id="",
            ts=base_time,
            input_hash="hash9",
            output_hash="hash10",
            payload_in={"patch": "diff...", **provenance},
            payload_out={"apply": "applied", "test_passed": True},
            tool_calls=[{"name": "git apply", "input": {}, "output": {"status": "applied"}}],
            tokens_in=0,
            tokens_out=0,
        ),
    ]
    return events


def test_zoo_specimen_creation(sink: AuditSink, sample_events: list[AuditEvent]) -> None:
    """Test creating a specimen from audit events."""
    for event in sample_events:
        sink.write_ahead(event)

    view = ZooView(sink)
    specimens = view.rebuild()

    assert len(specimens) == 1
    specimen = specimens[0]

    assert specimen.specimen_id == "evt-root-1"
    assert specimen.task == "Fix off-by-one in loop"
    assert "len(items) - 1" in specimen.patch
    assert "Empty list causes index error" in specimen.attack
    assert "misses empty list case" in specimen.critique
    assert specimen.verdict == "patch_fix"
    assert specimen.repo_url == "https://github.com/example/repo"
    assert specimen.license == "MIT"
    assert specimen.commit_sha == "abc123"
    assert specimen.bug_type == "off-by-one"
    assert len(specimen.audit_hashes) == 5
    assert specimen.tokens_total > 0
    assert not specimen.distilled


def test_zoo_specimen_serialization(sink: AuditSink, sample_events: list[AuditEvent]) -> None:
    """Test specimen JSON serialization round-trip."""
    for event in sample_events:
        sink.write_ahead(event)

    view = ZooView(sink)
    specimens = view.rebuild()
    specimen = specimens[0]

    # Serialize
    data = specimen.to_dict()
    assert data["specimen_id"] == "evt-root-1"
    assert isinstance(data["audit_hashes"], list)

    # Deserialize
    restored = ZooSpecimen.from_dict(data)
    assert restored.specimen_id == specimen.specimen_id
    assert restored.patch == specimen.patch
    assert restored.audit_hashes == specimen.audit_hashes


def test_zoo_multiple_sessions(sink: AuditSink, sample_events: list[AuditEvent]) -> None:
    """Test zoo with multiple sessions."""
    # Session 1
    for event in sample_events:
        sink.write_ahead(event)

    # Session 2 - different thread
    from datetime import datetime
    base_time = datetime(2026, 8, 19, 13, 0, 0)

    provenance2 = {
        "repo_url": "https://github.com/example/repo",
        "license": "MIT",
        "commit_sha": "def456",
        "bug_type": "race",
    }

    events2 = [
        AuditEvent(
            id="evt-root-2",
            parent_event_id=None,
            thread_id="thread-2",
            node="coder",
            agent_id="coder-1",
            model_id="nvidia/nemotron-3-super-120b-a12b",
            ts=base_time,
            input_hash="hash11",
            output_hash="hash12",
            payload_in={"task": "Fix race condition", **provenance2},
            payload_out={"patch": "diff --git a/bar.py b/bar.py\n+ lock.acquire()"},
            tool_calls=[],
            tokens_in=90,
            tokens_out=45,
        ),
        AuditEvent(
            id="evt-arb-2",
            parent_event_id="evt-root-2",
            thread_id="thread-2",
            node="arbiter",
            agent_id="arbiter-1",
            model_id="nvidia/nemotron-3-ultra-550b-a55b",
            ts=base_time,
            input_hash="hash13",
            output_hash="hash14",
            payload_in={"patch": "diff...", "attack": "", "rounds": 1, **provenance2},
            payload_out={"verdict": "pass", "escalated": False},
            tool_calls=[],
            tokens_in=80,
            tokens_out=25,
        ),
    ]
    for e in events2:
        sink.write_ahead(e)

    view = ZooView(sink)
    specimens = view.rebuild()

    assert len(specimens) == 2
    # Should be in chronological order
    assert specimens[0].specimen_id == "evt-root-1"
    assert specimens[1].specimen_id == "evt-root-2"
    assert specimens[0].bug_type == "off-by-one"
    assert specimens[1].bug_type == "race"


def test_zoo_export_jsonl(sink: AuditSink, sample_events: list[AuditEvent]) -> None:
    """Test exporting zoo to JSONL."""
    for event in sample_events:
        sink.write_ahead(event)

    view = ZooView(sink)

    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        output_path = Path(f.name)

    try:
        count = view.export_jsonl(output_path)
        assert count == 1

        # Verify content
        lines = output_path.read_text().strip().split("\n")
        assert len(lines) == 1
        import json
        data = json.loads(lines[0])
        assert data["specimen_id"] == "evt-root-1"
    finally:
        output_path.unlink(missing_ok=True)


def test_zoo_stats(sink: AuditSink, sample_events: list[AuditEvent]) -> None:
    """Test zoo statistics."""
    for event in sample_events:
        sink.write_ahead(event)

    view = ZooView(sink)
    stats = view.stats()

    assert stats["total"] == 1
    assert stats["distilled"] == 0
    assert stats["by_verdict"]["patch_fix"] == 1
    assert stats["by_bug_type"]["off-by-one"] == 1
    assert stats["total_tokens"] > 0


def test_curation_policy_permissive() -> None:
    """Test curation policy with permissive license."""
    policy = CurationPolicy()

    specimen = ZooSpecimen(
        specimen_id="test",
        task="test",
        patch="diff",
        attack="attack",
        critique="critique",
        verdict="pass",
        repo_url="https://github.com/test/repo",
        license="MIT",
        commit_sha="abc",
        bug_type="logic",
        audit_hashes=[],
        tool_calls=[],
        tokens_total=100,
        timestamp="2026-08-19T12:00:00",
    )

    assert policy.should_include(specimen)
    assert not policy.should_distill(specimen)


def test_curation_policy_non_permissive() -> None:
    """Test curation policy with non-permissive license."""
    policy = CurationPolicy()

    specimen = ZooSpecimen(
        specimen_id="test",
        task="test",
        patch="diff",
        attack="attack",
        critique="critique",
        verdict="pass",
        repo_url="https://github.com/test/repo",
        license="GPL-3.0",
        commit_sha="abc",
        bug_type="logic",
        audit_hashes=[],
        tool_calls=[],
        tokens_total=100,
        timestamp="2026-08-19T12:00:00",
    )

    assert policy.should_include(specimen)
    assert policy.should_distill(specimen)


def test_curation_policy_distillation() -> None:
    """Test specimen distillation for non-permissive license."""
    policy = CurationPolicy()

    specimen = ZooSpecimen(
        specimen_id="test",
        task="test",
        patch="diff --git a/foo.py b/foo.py\n- x\n+ y",
        attack="attack details",
        critique="critique details",
        verdict="pass",
        repo_url="https://github.com/test/repo",
        license="GPL-3.0",
        commit_sha="abc",
        bug_type="logic",
        audit_hashes=[("in1", "out1")],
        tool_calls=[],
        tokens_total=100,
        timestamp="2026-08-19T12:00:00",
    )

    view = ZooView.__new__(ZooView)  # Create without calling __init__
    view.curation_policy = policy

    distilled = view._distill(specimen)

    assert distilled.distilled
    assert "[DISTILLED]" in distilled.patch
    assert "[DISTILLED]" in distilled.attack
    assert "[DISTILLED]" in distilled.critique
    assert distilled.verdict == specimen.verdict
    assert distilled.audit_hashes == specimen.audit_hashes


def test_license_classifier_spdx() -> None:
    """Test SPDX license normalization."""
    assert LicenseClassifier.from_spdx("MIT") == "MIT"
    assert LicenseClassifier.from_spdx("mit") == "MIT"
    assert LicenseClassifier.from_spdx("Apache-2.0") == "Apache-2.0"
    assert LicenseClassifier.from_spdx("apache 2.0") == "Apache-2.0"
    assert LicenseClassifier.from_spdx("GPL-3.0") == "GPL-3.0"
    assert LicenseClassifier.from_spdx("Unknown") == "Unknown"


def test_license_classifier_is_permissive() -> None:
    """Test permissive license check."""
    assert LicenseClassifier.is_permissive("MIT")
    assert LicenseClassifier.is_permissive("Apache-2.0")
    assert LicenseClassifier.is_permissive("BSD-3-Clause")
    assert not LicenseClassifier.is_permissive("GPL-3.0")
    assert not LicenseClassifier.is_permissive("AGPL-3.0")
    assert not LicenseClassifier.is_permissive(None)


def test_zoo_empty(sink: AuditSink) -> None:
    """Test zoo with no events."""
    view = ZooView(sink)
    specimens = view.rebuild()
    assert specimens == []

    stats = view.stats()
    assert stats["total"] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
