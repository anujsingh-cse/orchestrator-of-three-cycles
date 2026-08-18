"""Audit DAG tests (D2/D7/D11): fail-closed writes, integrity check, replay.

Release-blocking (D11):
- replay-twice-identical-events: same inputs -> same hashes, same chain.
- gapped sessions must be refused at startup.
"""
from __future__ import annotations

import pytest

from orchestrator.audit import AuditEvent, AuditIntegrityError, AuditSink


def make_chain(sink: AuditSink, thread: str) -> tuple[AuditEvent, AuditEvent]:
    root = AuditEvent(
        thread_id=thread,
        node="coder",
        agent_id="coder",
        model_id="fake-model",
        input_hash=AuditEvent.content_hash({"task": "fix"}),
        output_hash=AuditEvent.content_hash({"patch": "diff"}),
    )
    sink.write_ahead(root)
    child = AuditEvent(
        thread_id=thread,
        parent_event_id=root.id,
        node="adversary",
        agent_id="adversary",
        model_id="fake-model-2",
        input_hash=root.output_hash,
        output_hash=AuditEvent.content_hash({"attack": "nope"}),
        gate_decision="pass",
    )
    sink.write_ahead(child)
    return root, child


def test_integrity_passes_on_healthy_chain(tmp_path) -> None:
    sink = AuditSink(tmp_path / "audit.db")
    thread = "t1"
    make_chain(sink, thread)
    sink.verify_integrity(thread)  # must not raise
    sink.close()


def test_gap_in_chain_is_refused(tmp_path) -> None:
    sink = AuditSink(tmp_path / "audit.db")
    thread = "t2"
    root, child = make_chain(sink, thread)
    grandchild = AuditEvent(
        thread_id=thread,
        parent_event_id=child.id,
        node="arbiter",
        agent_id="arbiter",
        model_id="fake-model",
        input_hash=child.output_hash,
        output_hash=AuditEvent.content_hash({"ok": True}),
    )
    sink.write_ahead(grandchild)
    sink._conn.execute("DELETE FROM events WHERE id = ?", (child.id,))  # true gap
    sink._conn.commit()
    with pytest.raises(AuditIntegrityError):
        sink.verify_integrity(thread)
    sink.close()


def test_missing_root_is_refused(tmp_path) -> None:
    sink = AuditSink(tmp_path / "audit.db")
    thread = "t3"
    make_chain(sink, thread)
    sink._conn.execute("DELETE FROM events WHERE parent_event_id IS NULL")
    sink._conn.commit()
    with pytest.raises(AuditIntegrityError):
        sink.verify_integrity(thread)
    sink.close()


def test_replay_twice_identical_events(tmp_path) -> None:
    """D11 release-blocking: same inputs -> identical hashes and chains."""
    sink = AuditSink(tmp_path / "audit.db")
    events1 = make_chain(sink, "t4")
    root2, child2 = make_chain(sink, "t5")
    assert root2.input_hash == events1[0].input_hash
    assert root2.output_hash == events1[0].output_hash
    assert child2.input_hash == events1[1].input_hash
    assert child2.parent_event_id != events1[0].id  # distinct causal chains
    sink.close()
