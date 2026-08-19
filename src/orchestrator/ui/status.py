"""TUI status pane (T12, D20, P3) — streaming-first, cuttable.

90% of the value is streaming prints (D20); the Textual pane just renders the
graph's ``updates`` stream live. A session driver runs the graph in a worker
thread and pushes updates into a Rich log.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from rich.text import Text

try:  # textual is optional until the pane is actually used
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Container, Horizontal, Vertical
    from textual.widgets import Footer, Header, Log, ProgressBar, Static
except ImportError:  # pragma: no cover - plain streaming still works
    App = object  # type: ignore[assignment,misc]
    ComposeResult = Any
    Container = Horizontal = Vertical = Static = Log = ProgressBar = object
    Header = Footer = Binding = object


@dataclass
class SessionSnapshot:
    """Current session state for the TUI."""

    thread_id: str = ""
    task: str = ""
    coder_rounds: int = 0
    adversary_rounds: int = 0
    current_node: str = ""
    last_verdict: str = ""
    tokens_total: int = 0
    elapsed_s: float = 0.0
    gate_pending: bool = False
    gate_prompt: str = ""
    is_done: bool = False
    error: str | None = None


class StatusApp(App):
    """Streams node updates from a session into a scrolling log with live state."""

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("p", "pause", "Pause"),
        Binding("r", "resume", "Resume"),
        Binding("a", "approve", "Approve (HITL)"),
        Binding("x", "reject", "Reject (HITL)"),
    ]

    CSS = """
    #state-panel {
        width: 40;
        border: solid $primary;
        padding: 1;
    }
    #log-panel {
        border: solid $secondary;
        padding: 1;
    }
    #progress-bar {
        height: 1;
        margin: 1 0;
    }
    Log {
        height: 1fr;
    }
    """

    def __init__(
        self,
        updates: Callable[[], Iterator[dict[str, Any]]],
        decide: Callable[[dict[str, Any]], str] | None = None,
    ) -> None:
        super().__init__()
        self._updates = updates
        self._decide = decide or (lambda _: "reject")
        self._snapshot = SessionSnapshot()
        self._paused = False
        self._task: asyncio.Task | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id="state-panel"):
                yield Static(id="task-display", expand=False)
                yield Static(id="rounds-display", expand=False)
                yield Static(id="verdict-display", expand=False)
                yield Static(id="tokens-display", expand=False)
                yield ProgressBar(id="progress-bar", total=100, show_eta=False)
                yield Static(id="gate-display", expand=False)
            with Vertical(id="log-panel"):
                yield Log(highlight=True, id="event-log", markup=True)
        yield Footer()

    async def on_mount(self) -> None:
        self._task = asyncio.create_task(self._run_updates())

    async def _run_updates(self) -> None:
        try:
            for update in self._updates():
                await self._process_update(update)
        except Exception as exc:  # noqa: BLE001
            self._snapshot.error = str(exc)
            self._refresh_state()
        finally:
            self._snapshot.is_done = True
            self._refresh_state()

    async def _process_update(self, update: dict[str, Any]) -> None:
        log = self.query_one("#event-log", Log)

        for node, payload in update.items():
            if node == "__interrupt__":
                self._snapshot.gate_pending = True
                default_prompt = "Approve applying this patch?"
                self._snapshot.gate_prompt = payload[0].value.get("prompt", default_prompt)
                self._refresh_state()
                # Wait for user decision
                decision = await self._wait_for_gate()
                # The decision is handled by the graph via Command(resume=...)
                self._snapshot.gate_pending = False
                log.write(Text(f"[GATE] {decision}", style="bold magenta"))
                continue

            self._snapshot.current_node = node
            self._update_from_node(node, payload)

            summary = _summarize(node, payload)
            style = _node_style(node)
            log.write(Text(f"[{node}] {summary}", style=style))

            self._refresh_state()

        self._snapshot.is_done = True
        self._refresh_state()

    def _update_from_node(self, node: str, payload: dict[str, Any]) -> None:
        if node == "coder":
            self._snapshot.coder_rounds = payload.get(
                "coder_rounds", self._snapshot.coder_rounds
            )
        elif node == "adversary":
            self._snapshot.adversary_rounds = payload.get(
                "adversary_rounds", self._snapshot.adversary_rounds
            )
        elif node == "arbiter":
            self._snapshot.last_verdict = payload.get("verdict", "")
        elif node == "runner":
            self._snapshot.test_passed = payload.get("test_passed")
        # Token counting would come from the audit events in real implementation

    def _refresh_state(self) -> None:
        """Update the state panel display."""
        task_disp = self.query_one("#task-display", Static)
        task_disp.update(f"[bold]Task:[/bold] {self._snapshot.task[:80]}")

        rounds_disp = self.query_one("#rounds-display", Static)
        rounds_disp.update(
            f"[bold]Rounds:[/bold] Coder={self._snapshot.coder_rounds} "
            f"Adversary={self._snapshot.adversary_rounds}"
        )

        verdict_disp = self.query_one("#verdict-display", Static)
        verdict_color = _verdict_color(self._snapshot.last_verdict)
        verdict_text = (
            f"[bold]Verdict:[/bold] "
            f"[{verdict_color}]{self._snapshot.last_verdict}[/{verdict_color}]"
        )
        verdict_disp.update(verdict_text)

        tokens_disp = self.query_one("#tokens-display", Static)
        tokens_disp.update(f"[bold]Tokens:[/bold] {self._snapshot.tokens_total:,}")

        gate_disp = self.query_one("#gate-display", Static)
        if self._snapshot.gate_pending:
            gate_msg = f"[bold yellow]⚠ HITL GATE:[/bold yellow] {self._snapshot.gate_prompt}"
            gate_disp.update(gate_msg)
        elif self._snapshot.is_done:
            if not self._snapshot.error:
                status = "[bold green]✓ DONE[/bold green]"
            else:
                status = f"[bold red]✗ ERROR: {self._snapshot.error}[/bold red]"
            gate_disp.update(status)
        else:
            gate_disp.update(f"[bold]Running...[/bold] {self._snapshot.current_node}")

    async def _wait_for_gate(self) -> str:
        """Wait for user to press approve/reject."""
        # In real implementation, this would wait for the binding action
        # For now, return the default decision
        return self._decide({"prompt": self._snapshot.gate_prompt})

    def action_approve(self) -> None:
        """Approve the HITL gate."""
        self._decide({"prompt": self._snapshot.gate_prompt, "decision": "approve"})

    def action_reject(self) -> None:
        """Reject the HITL gate."""
        self._decide({"prompt": self._snapshot.gate_prompt, "decision": "reject"})

    def action_pause(self) -> None:
        """Pause the session."""
        self._paused = True

    def action_resume(self) -> None:
        """Resume the session."""
        self._paused = False


def _node_style(node: str) -> str:
    styles = {
        "coder": "cyan",
        "adversary": "red",
        "critic": "yellow",
        "arbiter": "magenta",
        "gate": "bold yellow",
        "runner": "green",
    }
    return styles.get(node, "white")


def _verdict_color(verdict: str) -> str:
    if verdict == "pass":
        return "green"
    if verdict in ("patch_fix", "replan", "minor_fix", "rubric_fail"):
        return "yellow"
    if verdict in ("escalate", "budget_exhausted"):
        return "red"
    return "white"


def _summarize(node: str, payload: dict[str, Any]) -> str:
    if node in ("coder", "runner"):
        return f"patch {len(payload.get('patch', ''))} chars"
    if node == "arbiter":
        return f"verdict={payload.get('verdict')} escalated={payload.get('escalated')}"
    if node == "gate":
        return f"decision={payload.get('gate_decision')}"
    return f"{len(payload)} keys"


def stream_print(updates: Callable[[], Iterator[dict[str, Any]]]) -> None:
    """Plain-text fallback (D20): the same stream, no Textual dependency."""
    for update in updates():
        for node, payload in update.items():
            if node == "__interrupt__":
                print(f"[HITL] {payload[0].value.get('prompt', 'approve?')}")
            else:
                print(f"[{node}] {_summarize(node, payload)}")
    print("[done] session finished")


def run_tui(
    updates: Callable[[], Iterator[dict[str, Any]]],
    decide: Callable[[dict[str, Any]], str] | None = None,
) -> None:
    """Run the Textual TUI if available, otherwise fall back to plain print."""
    try:
        app = StatusApp(updates, decide)
        app.run()
    except Exception:  # pragma: no cover - Textual not available or terminal incompatible
        stream_print(updates)
