"""Shared pytest fixtures."""

from __future__ import annotations

import pytest
from dotenv import load_dotenv

load_dotenv()  # NIM_API_KEY from .env (gitignored) for local integration runs


@pytest.fixture()
def audit_db(tmp_path):
    from orchestrator.audit import AuditSink

    sink = AuditSink(tmp_path / "audit.db")
    yield sink
    sink.close()
