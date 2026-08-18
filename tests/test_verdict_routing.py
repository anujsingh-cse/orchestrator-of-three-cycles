"""T3 verdict routing table tests — pure, no graph compile needed."""

from __future__ import annotations

import pytest
from langgraph.graph import END

from orchestrator.graph.builder import (
    GATE_APPROVE,
    GATE_REJECT,
    route_from_arbiter,
    route_from_coder,
    route_from_record_gate,
    route_from_runner,
)
from orchestrator.graph.nodes import _parse_verdict
from orchestrator.graph.state import SessionState
from orchestrator.graph.verdict import Verdict, escalation_hit, touched_paths


def _state(**overrides: object) -> SessionState:
    base: dict[str, object] = {
        "coder_rounds": 1,
        "adversary_rounds": 0,
        "verdict": "",
        "gate_decision": None,
        "test_passed": None,
        "thread_id": "t",
    }
    base.update(overrides)
    return base  # type: ignore[return-value]


class TestRouteFromCoder:
    def test_adversary_budget_remaining(self) -> None:
        assert route_from_coder(_state(adversary_rounds=0)) == "adversary"

    def test_adversary_budget_spent_skips_to_critic(self) -> None:
        assert route_from_coder(_state(adversary_rounds=2)) == "critic"

    def test_adversary_budget_never_negative(self) -> None:
        assert route_from_coder(_state(adversary_rounds=5)) == "critic"


class TestRouteFromArbiter:
    @pytest.mark.parametrize("verdict", [Verdict.PASS.value, Verdict.ESCALATE.value])
    def test_human_gate_required(self, verdict: str) -> None:
        assert route_from_arbiter(_state(verdict=verdict)) == "gate"

    def test_budget_exhausted_ends(self) -> None:
        assert route_from_arbiter(_state(verdict=Verdict.BUDGET_EXHAUSTED.value)) == END

    @pytest.mark.parametrize(
        "verdict",
        [
            Verdict.PATCH_FIX.value,
            Verdict.RUBRIC_FAIL.value,
            Verdict.REPLAN.value,
            Verdict.MINOR_FIX.value,
        ],
    )
    def test_retry_verdicts_go_back_to_coder(self, verdict: str) -> None:
        assert route_from_arbiter(_state(verdict=verdict)) == "coder"

    def test_unknown_verdict_ends_fail_closed(self) -> None:
        assert route_from_arbiter(_state(verdict="garbage")) == END


class TestRouteFromRecordGate:
    def test_approve_runs_runner(self) -> None:
        assert route_from_record_gate(_state(gate_decision=GATE_APPROVE)) == "runner"

    def test_reject_ends(self) -> None:
        assert route_from_record_gate(_state(gate_decision=GATE_REJECT)) == END

    def test_no_decision_ends_fail_closed(self) -> None:
        assert route_from_record_gate(_state(gate_decision=None)) == END


class TestRouteFromRunner:
    def test_passed_tests_end(self) -> None:
        assert route_from_runner(_state(test_passed=True)) == END

    def test_failed_tests_retry_coder_within_budget(self) -> None:
        assert route_from_runner(_state(test_passed=False, coder_rounds=2)) == "coder"

    def test_failed_tests_exhausted_budget_ends(self) -> None:
        assert route_from_runner(_state(test_passed=False, coder_rounds=3)) == END


class TestEscalation:
    SAFE_PATCH = """diff --git a/src/main.py b/src/main.py
index 1111111..2222222 100644
--- a/src/main.py
+++ b/src/main.py
@@ -1,3 +1,4 @@
+def helper():
+    return 42
"""

    def test_touched_paths_parses_diff_headers(self) -> None:
        assert touched_paths(self.SAFE_PATCH) == ["src/main.py"]

    def test_safe_patch_has_no_escalations(self) -> None:
        assert escalation_hit(self.SAFE_PATCH) == []

    @pytest.mark.parametrize(
        "patch_path",
        [
            ".env",
            "secrets/token.txt",
            "secrets/token.txt.bak",
            ".github/workflows/ci.yml",
            "package-lock.json",
        ],
    )
    def test_guard_railed_paths_escalate(self, patch_path: str) -> None:
        patch = (
            f"diff --git a/{patch_path} b/{patch_path}\n--- a/{patch_path}\n+++ b/{patch_path}\n"
        )
        assert escalation_hit(patch) == [patch_path]

    def test_lock_json_anywhere_escalates(self) -> None:
        patch = "diff --git a/sub/dir/uv.lock b/sub/dir/uv.lock\n"
        assert escalation_hit(patch) == ["sub/dir/uv.lock"]

    def test_similar_safe_paths_do_not_escalate(self) -> None:
        assert escalation_hit("diff --git a/src/.env.example b/src/.env.example\n") == []


class TestVerdictParsing:
    def test_valid_verdict_line(self) -> None:
        assert _parse_verdict("verdict: pass\nbecause it is good") == Verdict.PASS

    def test_malformed_output_fails_closed_to_escalate(self) -> None:
        assert _parse_verdict("the patch is fine, ship it") == Verdict.ESCALATE

    def test_unknown_verdict_token_fails_closed(self) -> None:
        assert _parse_verdict("verdict: maybe") == Verdict.ESCALATE
