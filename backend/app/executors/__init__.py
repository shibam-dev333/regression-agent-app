"""Common executor interface — Web and Desktop both implement this.

The orchestrator (LangGraph) only sees ``Executor``. Concrete implementations
own the Playwright Page / pywinauto Application underneath.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ActionResult:
    """Outcome of a single executor action."""

    ok: bool
    detail: str = ""
    screenshot: Path | None = None


class Executor(ABC):
    """One launched client (Unity, Web, or Studio) wrapped for the agent."""

    name: str  # "web" | "unity" | "studio"

    @abstractmethod
    async def start(self) -> ActionResult: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def click(self, target: str) -> ActionResult:
        """``target`` is a natural locator (button text, automation id, CSS, ...)."""

    @abstractmethod
    async def type_text(self, target: str, text: str) -> ActionResult: ...

    @abstractmethod
    async def screenshot(self, dest: Path | None = None) -> ActionResult: ...

    @abstractmethod
    async def stream_frame(self) -> bytes:
        """Return a JPEG/PNG byte payload to push over the live-view WS."""

    @abstractmethod
    async def describe(self) -> str:
        """Cheap text dump of what's on screen — fed to the vision LLM as fallback."""
