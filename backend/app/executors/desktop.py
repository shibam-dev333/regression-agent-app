"""Desktop executor — pywinauto wrapper for Unity Client and OnBase Studio.

Both apps are WPF / Win32 hybrids. pywinauto's ``uia`` backend reads the
Microsoft UI Automation tree, which is what AccessibilityInsights / Inspect.exe
also use. Most Hyland controls expose either an ``AutomationId`` or a ``Name``,
so the locator strategy is:

 1. AutomationId   (most stable when set)
 2. Name           (visible label / text)
 3. Best-fuzzy match against descendants when neither hits

Live-view streaming uses ``mss`` to grab the actual top-level window region,
JPEG-encoded for the chat UI.

NOTE: pywinauto + mss are Windows-only. Import is deferred to ``start()`` so
the module is importable on non-Windows machines for type-checking + CI.
"""
from __future__ import annotations

import asyncio
import logging
import re
import subprocess
from io import BytesIO
from pathlib import Path
from typing import Any

from app.executors import ActionResult, Executor
from app.tools.clients_config import DesktopCfg, load_clients_config

log = logging.getLogger(__name__)


class DesktopExecutor(Executor):
    """Generic desktop executor; ``name`` distinguishes Unity vs Studio for streaming/logs."""

    def __init__(self, name: str, cfg: DesktopCfg):
        if name not in {"unity", "studio"}:
            raise ValueError(f"DesktopExecutor name must be 'unity' or 'studio', got {name!r}")
        self.name = name
        self.cfg = cfg
        self._proc: subprocess.Popen | None = None
        self._app: Any = None  # pywinauto Application
        self._window: Any = None  # main WindowSpecification
        self._hwnd: int | None = None

    async def start(self) -> ActionResult:
        from pywinauto import Application  # deferred — Windows-only
        from pywinauto.findwindows import ElementNotFoundError

        exe = Path(self.cfg.exe)
        if not exe.exists():
            return ActionResult(ok=False, detail=f"{self.name} exe not found: {exe}")

        args = [str(exe)]
        if self.name == "unity" and self.cfg.server_location:
            args += [f"/serverlocation={self.cfg.server_location}"]
        log.info("Launching %s: %s", self.name, " ".join(args))
        self._proc = subprocess.Popen(args)

        # Give the app a moment to draw its main window.
        await asyncio.sleep(self.cfg.startup_seconds)

        # Attach pywinauto and locate the main window.
        try:
            self._app = Application(backend="uia").connect(process=self._proc.pid, timeout=20)
        except ElementNotFoundError as e:
            return ActionResult(ok=False, detail=f"could not attach to {self.name} pid={self._proc.pid}: {e}")

        title_re = re.compile(self.cfg.window_title_regex)
        # Wait for a top-level window matching the title regex (poll up to 20s).
        for _ in range(20):
            for w in self._app.windows():
                try:
                    if title_re.search(w.window_text() or ""):
                        self._window = w
                        self._hwnd = w.handle
                        break
                except Exception:
                    continue
            if self._window:
                break
            await asyncio.sleep(1)

        if not self._window:
            return ActionResult(ok=False, detail=f"no window matching /{self.cfg.window_title_regex}/")
        return ActionResult(ok=True, detail=f"{self.name} ready (hwnd={self._hwnd})")

    async def stop(self) -> None:
        try:
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
        except Exception as e:
            log.warning("DesktopExecutor.stop error: %s", e)

    # ── locators ──────────────────────────────────────────────────────────

    def _find(self, target: str):
        """Walk descendants; return first whose AutomationId or Name matches ``target``."""
        if not self._window:
            return None
        target_low = target.lower()
        # Fast path: pywinauto's child_window with auto_id then control_name.
        try:
            ctl = self._window.child_window(auto_id=target)
            ctl.wait("exists", timeout=2)
            return ctl
        except Exception:
            pass
        try:
            ctl = self._window.child_window(title=target)
            ctl.wait("exists", timeout=2)
            return ctl
        except Exception:
            pass
        # Fallback: walk descendants and fuzzy-match Name.
        try:
            for d in self._window.descendants():
                try:
                    name = (d.window_text() or "").strip()
                    if name and target_low in name.lower():
                        return d
                except Exception:
                    continue
        except Exception:
            pass
        return None

    async def click(self, target: str) -> ActionResult:
        ctl = self._find(target)
        if ctl is None:
            return ActionResult(ok=False, detail=f"no UIA element matched {target!r}")
        try:
            ctl.click_input()
            return ActionResult(ok=True, detail=f"clicked {target!r}")
        except Exception as e:
            return ActionResult(ok=False, detail=f"click failed: {e}")

    async def type_text(self, target: str, text: str) -> ActionResult:
        ctl = self._find(target)
        if ctl is None:
            return ActionResult(ok=False, detail=f"no UIA element matched {target!r}")
        try:
            ctl.set_focus()
            ctl.type_keys(text, with_spaces=True, pause=0.02)
            return ActionResult(ok=True, detail=f"typed into {target!r}")
        except Exception as e:
            return ActionResult(ok=False, detail=f"type failed: {e}")

    # ── screenshots / live stream ─────────────────────────────────────────

    def _grab_window_jpeg(self, quality: int) -> bytes:
        import mss
        from PIL import Image

        # pywinauto rectangle on the main window
        rect = self._window.rectangle()
        bbox = {"left": rect.left, "top": rect.top, "width": rect.width(), "height": rect.height()}
        with mss.mss() as sct:
            raw = sct.grab(bbox)
        img = Image.frombytes("RGB", raw.size, raw.rgb)
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        return buf.getvalue()

    async def screenshot(self, dest: Path | None = None) -> ActionResult:
        jpg = await asyncio.to_thread(self._grab_window_jpeg, 85)
        if dest:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(jpg)
            return ActionResult(ok=True, detail=str(dest), screenshot=dest)
        return ActionResult(ok=True, detail=f"{len(jpg)} bytes")

    async def stream_frame(self) -> bytes:
        cfg = load_clients_config().stream
        return await asyncio.to_thread(self._grab_window_jpeg, cfg.jpeg_quality)

    async def describe(self) -> str:
        # Cheap textual dump for the LLM: visible Name + ControlType of each descendant.
        lines: list[str] = [f"[{self.name}] hwnd={self._hwnd} title={self._window.window_text()!r}"]
        try:
            for d in self._window.descendants():
                try:
                    name = (d.window_text() or "").strip()
                    ctype = d.element_info.control_type
                    if name:
                        lines.append(f"  {ctype}: {name}")
                except Exception:
                    continue
                if len(lines) > 200:
                    break
        except Exception as e:
            lines.append(f"  (descendant walk failed: {e})")
        return "\n".join(lines)


def make_unity_executor() -> DesktopExecutor:
    return DesktopExecutor("unity", load_clients_config().unity)


def make_studio_executor() -> DesktopExecutor:
    return DesktopExecutor("studio", load_clients_config().studio)
