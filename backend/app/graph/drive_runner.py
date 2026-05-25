"""Drive runner — the FSM that takes a Jira key and walks a test live.

Phase 2a (this file):
  1. Fetch the Jira issue via JiraClient (REST).
  2. If it's a Test Execution, scrape its linked Tests via XrayScraper.
     User picks one with ``pick <KEY>`` or we proceed directly if there's one.
  3. Scrape the picked Test's steps.
  4. Run the keyword router → pick web / unity / studio.
  5. Launch the chosen executor; start a background frame-streamer that pushes
     JPEGs to the WS so the user sees the app live in the chat UI.
  6. Show step 1 and wait for user verdict (``pass`` | ``fail <reason>`` |
     ``block <reason>`` | ``note <text>``). Advance through steps.

Phase 2b (next iteration) will replace the manual verdict step with a
vision-LLM action loop that attempts each step autonomously.

The session is one ``DriveSession`` per WebSocket connection. It owns the
executor lifecycle; ``stop()`` always tears it down.
"""
from __future__ import annotations

import asyncio
import base64
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

from app.executors import Executor
from app.executors.desktop import make_studio_executor, make_unity_executor
from app.executors.web import WebExecutor
from app.graph.router import RouteDecision, route_from_text
from app.tools.clients_config import load_clients_config
from app.tools.jira_scraper import JiraAuthError, JiraClient, XrayScraper

log = logging.getLogger(__name__)

Emit = Callable[[dict[str, Any]], Awaitable[None]]
AwaitingState = Literal["pick", "verdict", "confirm_client", None]


@dataclass
class DriveSession:
    emit: Emit
    issue: dict[str, Any] | None = None
    exec_tests: list[dict[str, Any]] = field(default_factory=list)
    test_steps: list[dict[str, Any]] = field(default_factory=list)
    test_key: str | None = None
    route_decision: RouteDecision | None = None
    executor: Executor | None = None
    step_idx: int = 0
    awaiting: AwaitingState = None
    _stream_task: asyncio.Task | None = None
    _stream_stop: asyncio.Event = field(default_factory=asyncio.Event)

    # ── lifecycle ─────────────────────────────────────────────────────────

    async def stop(self) -> None:
        self._stream_stop.set()
        if self._stream_task:
            try:
                await asyncio.wait_for(self._stream_task, timeout=2.0)
            except (TimeoutError, asyncio.TimeoutError):
                self._stream_task.cancel()
        if self.executor:
            try:
                await self.executor.stop()
            except Exception as e:
                log.warning("executor stop error: %s", e)
        self.executor = None
        self.awaiting = None

    # ── entry: user typed ``run <KEY>`` ───────────────────────────────────

    async def start_run(self, key: str) -> None:
        await self._emit_status(f"fetching {key} from Jira…")
        try:
            issue = await JiraClient().fetch_issue(key)
        except JiraAuthError as e:
            await self._emit_error(str(e))
            return
        except Exception as e:
            await self._emit_error(f"Jira fetch failed: {e}")
            return
        self.issue = issue
        await self.emit(
            {
                "type": "issue",
                "key": issue["key"],
                "summary": issue["summary"],
                "issue_type": issue["issue_type"],
                "status": issue["status"],
                "url": issue["url"],
            }
        )

        if issue["issue_type"] == "Test Execution":
            await self._handle_execution(key)
        elif issue["issue_type"] in {"Xray Test", "Test"}:
            self.test_key = key
            await self._load_test_and_route()
        else:
            await self._emit_error(
                f"issue type {issue['issue_type']!r} is not a Test or Test Execution"
            )

    async def _handle_execution(self, exec_key: str) -> None:
        await self._emit_status("scraping linked tests (Xray iframe)…")
        try:
            async with XrayScraper(headless=True) as scraper:
                result = await scraper.fetch_execution_tests(exec_key)
        except JiraAuthError:
            await self._emit_error("not logged in to Jira — run `scraper login` once on the VM.")
            return
        except Exception as e:
            log.exception("Xray execution scrape failed for %s", exec_key)
            await self._emit_error(f"Xray scrape failed: {type(e).__name__}: {e!r}")
            return
        self.exec_tests = result.get("tests", [])
        if not self.exec_tests:
            await self._emit_error(
                "no linked tests found in the Xray panel — the iframe selector may need tuning"
            )
            return
        await self.emit(
            {
                "type": "tests",
                "exec_key": exec_key,
                "tests": self.exec_tests,
            }
        )
        await self._emit_status(
            f"{len(self.exec_tests)} test(s) in execution — reply `pick <KEY>` to drive one"
        )
        self.awaiting = "pick"

    async def _load_test_and_route(self) -> None:
        assert self.test_key
        await self._emit_status(f"scraping steps for {self.test_key}…")
        try:
            async with XrayScraper(headless=True) as scraper:
                result = await scraper.fetch_xray_test_steps(self.test_key)
        except Exception as e:
            await self._emit_error(f"step scrape failed: {e}")
            return
        self.test_steps = result.get("steps", [])
        if not self.test_steps:
            await self._emit_error("no steps found in Xray panel — selectors may need tuning")
            return
        await self.emit(
            {
                "type": "steps",
                "test_key": self.test_key,
                "count": len(self.test_steps),
            }
        )

        summary = (self.issue or {}).get("summary", "") if self.issue else ""
        first_action = self.test_steps[0].get("action", "")
        decision = route_from_text(summary, first_step=first_action)
        self.route_decision = decision
        await self.emit(
            {
                "type": "route",
                "client": decision.client,
                "confidence": decision.confidence,
                "reason": decision.reason,
            }
        )
        if decision.client == "manual":
            try:
                web_url = load_clients_config().web.url
            except Exception:
                web_url = ""
            if web_url:
                await self._emit_status(
                    f"router unsure — defaulting to web ({web_url}). "
                    "Type `use unity` or `use studio` within 3s to override."
                )
                await self._launch("web")
                return
            await self._emit_status(
                "router could not pick a client — reply `use web|unity|studio` to override"
            )
            self.awaiting = "confirm_client"
            return
        await self._launch(decision.client)

    # ── launch + streaming ────────────────────────────────────────────────

    async def _launch(self, client: str, override_url: str | None = None) -> None:
        await self._emit_status(
            f"launching {client} client…" + (f" ({override_url})" if override_url else "")
        )
        try:
            if client == "web":
                cfg = load_clients_config().web
                if override_url:
                    from dataclasses import replace
                    cfg = replace(cfg, url=override_url)
                self.executor = WebExecutor(cfg=cfg)
            elif client == "unity":
                self.executor = make_unity_executor()
            elif client == "studio":
                self.executor = make_studio_executor()
            else:
                await self._emit_error(f"unknown client: {client}")
                return
            result = await self.executor.start()
        except Exception as e:
            log.exception("executor.start() failed for %s", client)
            await self._emit_error(f"launch failed: {type(e).__name__}: {e!r}")
            return
        if not result.ok:
            await self._emit_error(result.detail)
            return
        await self._emit_status(result.detail)
        self._stream_stop.clear()
        self._stream_task = asyncio.create_task(self._stream_loop())
        self.step_idx = 0
        await self._show_step()

    async def _stream_loop(self) -> None:
        cfg = load_clients_config().stream
        interval = max(0.1, 1.0 / max(1, cfg.fps))
        while not self._stream_stop.is_set():
            if not self.executor:
                break
            try:
                jpg = await self.executor.stream_frame()
                await self.emit(
                    {
                        "type": "frame",
                        "client": self.executor.name,
                        "jpg_b64": base64.b64encode(jpg).decode("ascii"),
                    }
                )
            except Exception as e:
                log.debug("stream frame error: %s", e)
                await asyncio.sleep(1.0)
                continue
            try:
                await asyncio.wait_for(self._stream_stop.wait(), timeout=interval)
            except (TimeoutError, asyncio.TimeoutError):
                pass

    async def _show_step(self) -> None:
        if self.step_idx >= len(self.test_steps):
            await self._emit_status("✓ all steps complete")
            await self.stop()
            return
        step = self.test_steps[self.step_idx]
        await self.emit(
            {
                "type": "step",
                "idx": self.step_idx + 1,
                "total": len(self.test_steps),
                "action": step.get("action", ""),
                "data": step.get("data", ""),
                "expected": step.get("expected", ""),
            }
        )
        # Try the vision action loop first (web client only for now).
        if self.executor and self.executor.name == "web":
            # If the step is just "login" and auto-login already succeeded,
            # skip the LLM and pass immediately.
            action_lc = (step.get("action", "") or "").lower()
            if (
                getattr(self.executor, "logged_in", False)
                and ("login" in action_lc or "log in" in action_lc or "sign in" in action_lc)
            ):
                await self._emit_status(
                    f"agent → step {self.step_idx + 1} PASS (auto-login already succeeded)"
                )
                self.step_idx += 1
                await self._show_step()
                return
            from app.graph.action_loop import run_step

            await self._emit_status(f"agent driving step {self.step_idx + 1}…")
            try:
                result = await run_step(self.executor, step)
            except Exception as e:
                log.exception("action loop crashed")
                await self._emit_error(f"action loop error: {type(e).__name__}: {e!r}")
                self.awaiting = "verdict"
                return
            await self.emit(
                {
                    "type": "agent_step_result",
                    "idx": self.step_idx + 1,
                    "verdict": result.verdict,
                    "reason": result.reason,
                    "history": result.history,
                }
            )
            if result.verdict == "pass":
                await self._emit_status(f"agent → step {self.step_idx + 1} PASS")
                self.step_idx += 1
                await self._show_step()
                return
            if result.verdict == "fail":
                await self._emit_status(
                    f"agent → step {self.step_idx + 1} FAIL: {result.reason}"
                )
                await self._emit_status("(bug-draft is Phase F; skipping)")
                self.step_idx += 1
                await self._show_step()
                return
            # stuck → hand over to human
            await self._emit_status(
                f"agent stuck on step {self.step_idx + 1} ({result.reason}). "
                "Take over: type `pass`, `fail <reason>`, `block <reason>`, or `stop`."
            )
            self.awaiting = "verdict"
            return
        # non-web: manual mode
        self.awaiting = "verdict"

    # ── user replies during a run ─────────────────────────────────────────

    async def handle_command(self, text: str) -> bool:
        """Return True if the text was consumed as a drive command."""
        t = text.strip()
        low = t.lower()
        if low.startswith("run "):
            await self.stop()
            key = t.split(None, 1)[1].strip().rstrip("/").split("/")[-1].upper()
            await self.start_run(key)
            return True
        if low == "stop":
            await self._emit_status("stopping…")
            await self.stop()
            await self._emit_status("stopped.")
            return True

        if self.awaiting == "pick" and low.startswith("pick "):
            picked = t.split(None, 1)[1].strip().upper()
            if not any(t_["key"].upper() == picked for t_ in self.exec_tests):
                await self._emit_error(f"{picked!r} is not in the linked tests list")
                return True
            self.test_key = picked
            self.awaiting = None
            await self._load_test_and_route()
            return True

        if self.awaiting == "confirm_client" and low.startswith("use "):
            parts = t.split(None, 2)
            picked = parts[1].lower() if len(parts) > 1 else ""
            override_url = parts[2].strip() if len(parts) > 2 else None
            if picked not in {"web", "unity", "studio"}:
                await self._emit_error("use web|unity|studio [optional URL for web]")
                return True
            self.awaiting = None
            await self._launch(picked, override_url=override_url)
            return True

        if self.awaiting == "verdict":
            if low == "pass":
                await self._emit_status(f"step {self.step_idx + 1} PASS")
                self.step_idx += 1
                await self._show_step()
                return True
            if low.startswith("fail"):
                reason = t[5:].strip() if len(t) > 5 else "(no reason)"
                await self._emit_status(f"step {self.step_idx + 1} FAIL — {reason}")
                await self._emit_status("(bug-draft is Phase F; skipping for now)")
                self.step_idx += 1
                await self._show_step()
                return True
            if low.startswith("block"):
                reason = t[6:].strip() if len(t) > 6 else "(no reason)"
                await self._emit_status(f"BLOCKED — {reason}")
                await self.stop()
                return True
            if low.startswith("note"):
                note = t[5:].strip() if len(t) > 5 else ""
                await self._emit_status(f"note: {note}")
                return True

        return False

    # ── emit helpers ──────────────────────────────────────────────────────

    async def _emit_status(self, msg: str) -> None:
        await self.emit({"type": "status", "text": msg})

    async def _emit_error(self, msg: str) -> None:
        await self.emit({"type": "error", "text": msg})


def is_drive_command(text: str, session: DriveSession | None) -> bool:
    """Cheap precheck so chat.py can route without instantiating a session."""
    low = text.strip().lower()
    if low.startswith("run ") or low == "stop":
        return True
    if session and session.awaiting:
        return True
    return False
