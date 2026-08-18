"""T12 TUI smoke tests — construction + plain-stream fallback, no deadlock."""

from __future__ import annotations


def test_stream_print_iterates_without_deadlock(capsys: object) -> None:
    from orchestrator.ui.status import stream_print

    calls = 0

    def updates():
        nonlocal calls
        calls += 1
        yield {"coder": {"patch": "x" * 10}}
        yield {"__interrupt__": [type("I", (), {"value": {"prompt": "approve?"}})()]}

    stream_print(updates)
    captured = capsys.readouterr()  # type: ignore[union-attr]
    assert "[coder]" in captured.out
    assert "[HITL]" in captured.out
    assert "[done]" in captured.out
    assert calls == 1  # generator consumed once


def test_status_app_constructs_without_import_error() -> None:
    from orchestrator.ui.status import StatusApp

    app = StatusApp(lambda: iter([]))
    assert app is not None
