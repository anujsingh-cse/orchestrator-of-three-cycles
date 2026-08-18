"""Audit sink — fail-closed, write-ahead, startup integrity check (D2/D7)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from orchestrator.audit.event import AuditEvent


class AuditIntegrityError(RuntimeError):
    """The causal chain has gaps, cycles, or hash mismatches — refuse replay."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    parent_event_id TEXT,
    thread_id TEXT NOT NULL,
    node TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    output_hash TEXT NOT NULL,
    tool_calls TEXT NOT NULL DEFAULT '[]',
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    gate_decision TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_thread ON events (thread_id, ts);
CREATE INDEX IF NOT EXISTS idx_events_parent ON events (parent_event_id);
"""


class AuditSink:
    """Append-only SQLite store; the ONLY source of truth for session state.

    D7 semantics:
    - ``write_ahead`` persists the event BEFORE the transition it records;
      callers treat the returned id as the authority handle.
    - Fail-closed: every write path raises on error; graph nodes must
      propagate (never swallow).
    - ``verify_integrity`` runs at startup and refuses gapped replays.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._conn = sqlite3.connect(str(path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def write_ahead(self, event: AuditEvent) -> str:
        """Persist before the transition; returns the authority id."""
        try:
            self._conn.execute(
                "INSERT INTO events (id, parent_event_id, thread_id, node, agent_id, model_id, ts,"
                " input_hash, output_hash, tool_calls, tokens_in, tokens_out, gate_decision)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event.id,
                    event.parent_event_id,
                    event.thread_id,
                    event.node,
                    event.agent_id,
                    event.model_id,
                    event.ts.isoformat(),
                    event.input_hash,
                    event.output_hash,
                    json_dumps(event.tool_calls),
                    event.tokens_in,
                    event.tokens_out,
                    event.gate_decision,
                ),
            )
            self._conn.commit()
        except sqlite3.Error as exc:  # fail-closed: nothing swallowed
            raise AuditIntegrityError(f"write-ahead failed: {exc}") from exc
        return event.id

    def session_events(self, thread_id: str) -> list[AuditEvent]:
        rows = self._conn.execute(
            "SELECT * FROM events WHERE thread_id = ? ORDER BY ts, rowid", (thread_id,)
        ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def children_of(self, parent_id: str) -> list[AuditEvent]:
        rows = self._conn.execute(
            "SELECT * FROM events WHERE parent_event_id = ? ORDER BY ts, rowid", (parent_id,)
        ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def verify_integrity(self, thread_id: str) -> None:
        """Walk the causal chain; raise on gaps, cycles, or missing roots.

        A session root is an event with ``parent_event_id IS NULL``; every
        non-root must reference an existing parent, and the walk must reach
        every event exactly once.
        """
        events = self.session_events(thread_id)
        by_id = {e.id: e for e in events}
        roots = [e for e in events if e.parent_event_id is None]
        if not roots:
            raise AuditIntegrityError(f"thread {thread_id}: no root event")
        if len(roots) > 1:
            raise AuditIntegrityError(f"thread {thread_id}: {len(roots)} roots — expected 1")
        visited: set[str] = set()
        stack = [roots[0].id]
        while stack:
            current = stack.pop()
            if current in visited:
                raise AuditIntegrityError(f"thread {thread_id}: cycle at {current}")
            visited.add(current)
            for child in by_id.values():
                if child.parent_event_id == current:
                    stack.append(child.id)
        missing = set(by_id) - visited
        if missing:
            raise AuditIntegrityError(f"thread {thread_id}: orphaned events {sorted(missing)}")

    def close(self) -> None:
        self._conn.close()

    def _row_to_event(self, row: sqlite3.Row) -> AuditEvent:
        return AuditEvent(
            id=row[0],
            parent_event_id=row[1],
            thread_id=row[2],
            node=row[3],
            agent_id=row[4],
            model_id=row[5],
            ts=row[6],
            input_hash=row[7],
            output_hash=row[8],
            tool_calls=json_loads(row[9]),
            tokens_in=row[10],
            tokens_out=row[11],
            gate_decision=row[12],
        )


def json_dumps(value: object) -> str:
    import json

    return json.dumps(value, separators=(",", ":"))


def json_loads(value: str) -> list[dict[str, object]]:
    import json

    return json.loads(value)
