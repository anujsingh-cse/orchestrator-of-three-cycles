"""Tests for TUI status pane (T12, D20)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from orchestrator.ui.status import (
    SessionSnapshot,
    _node_style,
    _summarize,
    _verdict_color,
    stream_print,
)


def make_update(node: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {node: payload}


def test_summarize_coder() -> None:
    update = make_update("coder", {"patch": "diff --git a/foo.py b/foo.py\n+ x = 1"})
    assert _summarize("coder", update["coder"]) == "patch 36 chars"


def test_summarize_arbiter() -> None:
    update = make_update("arbiter", {"verdict": "pass", "escalated": False})
    assert _summarize("arbiter", update["arbiter"]) == "verdict=pass escalated=False"


def test_summarize_gate() -> None:
    update = make_update("gate", {"gate_decision": "approve"})
    assert _summarize("gate", update["gate"]) == "decision=approve"


def test_summarize_other() -> None:
    update = make_update("critic", {"critique": "some critique", "tokens": 100})
    assert _summarize("critic", update["critic"]) == "2 keys"


def test_node_style_known() -> None:
    assert _node_style("coder") == "cyan"
    assert _node_style("adversary") == "red"
    assert _node_style("critic") == "yellow"
    assert _node_style("arbiter") == "magenta"
    assert _node_style("gate") == "bold yellow"
    assert _node_style("runner") == "green"
    assert _node_style("unknown") == "white"


def test_verdict_color() -> None:
    assert _verdict_color("pass") == "green"
    assert _verdict_color("patch_fix") == "yellow"
    assert _verdict_color("replan") == "yellow"
    assert _verdict_color("minor_fix") == "yellow"
    assert _verdict_color("rubric_fail") == "yellow"
    assert _verdict_color("escalate") == "red"
    assert _verdict_color("budget_exhausted") == "red"
    assert _verdict_color("unknown") == "white"


def test_session_snapshot_defaults() -> None:
    snap = SessionSnapshot()
    assert snap.thread_id == ""
    assert snap.task == ""
    assert snap.coder_rounds == 0
    assert snap.adversary_rounds == 0
    assert snap.current_node == ""
    assert snap.last_verdict == ""
    assert snap.tokens_total == 0
    assert snap.elapsed_s == 0.0
    assert snap.gate_pending is False
    assert snap.gate_prompt == ""
    assert snap.is_done is False
    assert snap.error is None


def test_stream_print() -> None:
    """Test plain-text streaming fallback."""
    def updates() -> Iterator[dict[str, Any]]:
        yield {"coder": {"patch": "diff --git a/foo.py b/foo.py\n+ x = 1"}}
        yield {"adversary": {"attack": "empty list"}}
        yield {"arbiter": {"verdict": "pass", "escalated": False}}

    # Should not raise
    stream_print(updates)


def test_session_snapshot_with_data() -> None:
    snap = SessionSnapshot(
        thread_id="thread-1",
        task="Fix off-by-one",
        coder_rounds=2,
        adversary_rounds=1,
        current_node="arbiter",
        last_verdict="pass",
        tokens_total=1500,
        elapsed_s=12.5,
        gate_pending=False,
        is_done=True,
    )
    assert snap.thread_id == "thread-1"
    assert snap.coder_rounds == 2
    assert snap.last_verdict == "pass"
    assert snap.is_done is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
