"""Terminal output and prompts used by CLI command handlers."""

from __future__ import annotations

from typing import Protocol

from rich.console import Console


class Output(Protocol):
    def print(self, message: str = "") -> None:
        """Render one message."""

    def confirm(self, prompt: str) -> bool:
        """Return whether the user approved an action."""


class ConsoleOutput:
    def __init__(self, console: Console | None = None) -> None:
        self._console = console or Console()

    def print(self, message: str = "") -> None:
        self._console.print(message)

    def confirm(self, prompt: str) -> bool:
        return input(prompt).strip().casefold() != "n"


__all__ = ["ConsoleOutput", "Output"]
