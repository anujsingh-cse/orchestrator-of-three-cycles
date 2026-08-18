"""AuditEvent schema — the causal DAG record (D2: sole authority)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def sha256_canonical(payload: dict[str, Any]) -> str:
    """Stable hash: sorted keys, compact separators — replay-deterministic."""
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class AuditEvent(BaseModel):
    """One node execution record in the causal DAG.

    D7 contract: the row is persisted *before* the transition it records
    (write-ahead); a write failure HALTS the graph (fail-closed).
    """

    id: str = Field(default_factory=lambda: uuid4().hex)
    parent_event_id: str | None = None
    thread_id: str
    node: str
    agent_id: str
    model_id: str
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    input_hash: str
    output_hash: str
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    gate_decision: str | None = None

    @staticmethod
    def content_hash(payload: dict[str, Any]) -> str:
        """Hash any node payload (state snapshot) for replay comparison."""
        return sha256_canonical(payload)
