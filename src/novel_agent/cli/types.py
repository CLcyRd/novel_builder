"""CLI presentation result shared by the shell and command handlers."""

from __future__ import annotations

from dataclasses import dataclass

from novel_agent.agent.freeform import RunResult


@dataclass
class CLIResult:
    text: str
    exit_requested: bool = False
    clear_screen: bool = False
    run: RunResult | None = None
    artifact_text: str = ""
