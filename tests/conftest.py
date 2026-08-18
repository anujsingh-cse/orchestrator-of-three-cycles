"""Shared pytest fixtures."""
from __future__ import annotations

import pytest


@pytest.fixture()
def audit_db(tmp_path):
    from orchestrator.audit import AuditSink

    sink = AuditSink(tmp_path / "audit.db")
    yield sink
    sink.close()
