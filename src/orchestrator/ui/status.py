"""TUI status pane (T12, D20, P3) — streaming-first, cuttable.

90% of the value is streaming prints (D20); the Textual pane just renders the
graph's ``updates`` stream live. A session driver runs the graph in a worker
thread and pushes updates into a Rich log.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from rich.text import Text

try:  # textual is optional until the pane is actually used
    from textual.app import App, ComposeResult
    from textual.widgets import Header, Log
except ImportError:  # pragma: no cover - plain streaming still works
    App = object  # type: ignore[assignment,misc]


class StatusApp(App):
    """Streams node updates from a session into a scrolling log."""

    def __init__(self, updates: Callable[[], Iterator[dict[str, Any]]]) -> None:
        super().__init__()
        self._updates = updates

    def compose(self) -> ComposeResult:
        yield Header()
        yield Log(highlight=True)

    def on_mount(self) -> None:
        log = self.query_one(Log)
        for update in self._updates():
            for node, payload in update.items():
                if node == "__interrupt__":
                    log.write(
                        Text(
                            f"[HITL] {payload[0].value.get('prompt', 'approve?')}",
                            style="bold yellow",
                        )
                    )
                    continue
                log.write(Text(f"[{node}] {_summarize(node, payload)}"))
        log.write(Text("[done] session finished", style="bold green"))


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
