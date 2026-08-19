"""Failure zoo view — materialized view over the audit DAG (T9, D9, D19).

The zoo is a materialized view over the audit DAG (D2):
- Specimen ID = root AuditEvent id, rebuildable by replay
- Provenance (source repo + license + commit) on every specimen
- Non-permissive specimens excluded or distilled to synthetic equivalents
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from orchestrator.audit.event import AuditEvent
from orchestrator.audit.sink import AuditSink
from orchestrator.zoo.curation import CurationPolicy


@dataclass(frozen=True)
class ZooSpecimen:
    """A single failure specimen in the zoo.

    Attributes:
        specimen_id: Root AuditEvent ID (causal DAG root)
        task: Original task description
        patch: The proposed patch (unified diff)
        attack: Adversary's falsification
        critique: Critic's rubric verdict
        verdict: Final arbiter verdict
        repo_url: Source repository URL
        license: Repository license (SPDX identifier)
        commit_sha: Commit SHA where bug was seeded
        bug_type: Bug type tag (logic/off-by-one/rename/race/config)
        audit_hashes: List of (input_hash, output_hash) pairs for reproducibility
        tool_calls: Tool calls made during the session
        tokens_total: Total tokens consumed
        timestamp: ISO timestamp of specimen creation
        distilled: Whether this is a distilled (synthetic) specimen
    """

    specimen_id: str
    task: str
    patch: str
    attack: str
    critique: str
    verdict: str
    repo_url: str
    license: str
    commit_sha: str
    bug_type: str
    audit_hashes: list[tuple[str, str]]
    tool_calls: list[dict[str, Any]]
    tokens_total: int
    timestamp: str
    distilled: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Convert tuple to list for JSON serialization
        d["audit_hashes"] = [list(h) for h in self.audit_hashes]
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ZooSpecimen:
        data = dict(data)
        data["audit_hashes"] = [tuple(h) for h in data.get("audit_hashes", [])]
        return cls(**data)


class ZooView:
    """Materialized view of the failure zoo over the audit DAG.

    The zoo is rebuilt by replaying the audit DAG — no separate storage.
    Each specimen corresponds to a completed falsification loop session.
    """

    def __init__(
        self,
        sink: AuditSink,
        curation_policy: CurationPolicy | None = None,
    ) -> None:
        self.sink = sink
        self.curation_policy = curation_policy or CurationPolicy()

    def rebuild(self) -> list[ZooSpecimen]:
        """Rebuild the zoo by replaying the audit DAG.

        Returns specimens in chronological order (oldest first).
        """
        events = self.sink.read_all()
        if not events:
            return []

        # Group events by thread_id (session)
        sessions: dict[str, list[AuditEvent]] = {}
        for event in events:
            sessions.setdefault(event.thread_id, []).append(event)

        specimens: list[ZooSpecimen] = []
        for thread_id, session_events in sessions.items():
            # Sort by timestamp
            session_events.sort(key=lambda e: e.ts)

            specimen = self._build_specimen(thread_id, session_events)
            if specimen and self.curation_policy.should_include(specimen):
                if self.curation_policy.should_distill(specimen):
                    specimen = self._distill(specimen)
                specimens.append(specimen)

        return specimens

    def _build_specimen(
        self, thread_id: str, events: list[AuditEvent]
    ) -> ZooSpecimen | None:
        """Build a specimen from a session's audit events."""
        # Find root event (coder node with no parent)
        root_events = [e for e in events if e.parent_event_id is None]
        if not root_events:
            return None
        root = root_events[0]

        # Extract key events by node type
        coder_events = [e for e in events if e.node == "coder"]
        adversary_events = [e for e in events if e.node == "adversary"]
        critic_events = [e for e in events if e.node == "critic"]
        arbiter_events = [e for e in events if e.node == "arbiter"]

        if not coder_events or not arbiter_events:
            return None  # Incomplete session

        # Get the latest patch, attack, critique, verdict
        last_coder = coder_events[-1]
        last_adversary = adversary_events[-1] if adversary_events else None
        last_critic = critic_events[-1] if critic_events else None
        last_arbiter = arbiter_events[-1]

        patch = last_coder.payload_out.get("patch", "")
        attack = last_adversary.payload_out.get("attack", "") if last_adversary else ""
        critique = last_critic.payload_out.get("critique", "") if last_critic else ""
        verdict = last_arbiter.payload_out.get("verdict", "")

        # Collect audit hashes for reproducibility
        audit_hashes = [(e.input_hash, e.output_hash) for e in events]

        # Collect all tool calls
        tool_calls: list[dict[str, Any]] = []
        for e in events:
            tool_calls.extend(e.tool_calls)

        # Sum tokens
        tokens_total = sum(e.tokens_in + e.tokens_out for e in events)

        # Get provenance from root event payload
        repo_url = root.payload_in.get("repo_url", "")
        license_spdx = root.payload_in.get("license", "")
        commit_sha = root.payload_in.get("commit_sha", "")
        bug_type = root.payload_in.get("bug_type", "unknown")

        return ZooSpecimen(
            specimen_id=root.id,
            task=root.payload_in.get("task", ""),
            patch=patch,
            attack=attack,
            critique=critique,
            verdict=verdict,
            repo_url=repo_url,
            license=license_spdx,
            commit_sha=commit_sha,
            bug_type=bug_type,
            audit_hashes=audit_hashes,
            tool_calls=tool_calls,
            tokens_total=tokens_total,
            timestamp=(
                root.ts.isoformat()
                if hasattr(root, "ts") and root.ts
                else datetime.utcnow().isoformat()
            ),
            distilled=False,
        )

    def _distill(self, specimen: ZooSpecimen) -> ZooSpecimen:
        """Create a distilled (synthetic) version of a non-permissive specimen.

        Distillation removes verbatim code, keeping only:
        - Hashes, diffs, test names, bug type
        - No verbatim source code from the original repo
        """
        # Replace patch with a hash-only representation
        import hashlib
        patch_hash = hashlib.sha256(specimen.patch.encode()).hexdigest()[:16]
        msg = "original patch removed for licensing"
        distilled_patch = f"[DISTILLED] patch hash: {patch_hash} ({msg})"

        # Replace attack/critique with summaries
        distilled_attack = (
            f"[DISTILLED] {len(specimen.attack)} chars removed"
            if specimen.attack
            else ""
        )
        distilled_critique = (
            f"[DISTILLED] {len(specimen.critique)} chars removed"
            if specimen.critique
            else ""
        )

        return ZooSpecimen(
            specimen_id=specimen.specimen_id,
            task=specimen.task,
            patch=distilled_patch,
            attack=distilled_attack,
            critique=distilled_critique,
            verdict=specimen.verdict,
            repo_url=specimen.repo_url,
            license=specimen.license,
            commit_sha=specimen.commit_sha,
            bug_type=specimen.bug_type,
            audit_hashes=specimen.audit_hashes,
            tool_calls=specimen.tool_calls,
            tokens_total=specimen.tokens_total,
            timestamp=specimen.timestamp,
            distilled=True,
        )

    def export_jsonl(self, output_path: Path) -> int:
        """Export specimens to JSONL file."""
        specimens = self.rebuild()
        with output_path.open("w", encoding="utf-8") as f:
            for specimen in specimens:
                f.write(json.dumps(specimen.to_dict()) + "\n")
        return len(specimens)

    def stats(self) -> dict[str, Any]:
        """Get zoo statistics."""
        specimens = self.rebuild()
        if not specimens:
            return {"total": 0}

        by_verdict: dict[str, int] = {}
        by_bug_type: dict[str, int] = {}
        distilled_count = 0
        total_tokens = 0

        for s in specimens:
            by_verdict[s.verdict] = by_verdict.get(s.verdict, 0) + 1
            by_bug_type[s.bug_type] = by_bug_type.get(s.bug_type, 0) + 1
            if s.distilled:
                distilled_count += 1
            total_tokens += s.tokens_total

        return {
            "total": len(specimens),
            "distilled": distilled_count,
            "by_verdict": by_verdict,
            "by_bug_type": by_bug_type,
            "total_tokens": total_tokens,
            "avg_tokens_per_specimen": total_tokens // len(specimens) if specimens else 0,
        }
