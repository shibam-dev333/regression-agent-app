"""Vision action loop — drives one Xray step against a WebExecutor.

Per iteration:
  1. Capture a JPEG of the current page.
  2. Build a multimodal chat message: step text + expected + recent history + image.
  3. Ask the LLM for ONE next action as JSON.
  4. Execute it. Loop until ``done`` or budget exhausted.

LLM response contract (must be a single JSON object, nothing else):
  { "op": "click",   "target": "Workflow" }
  { "op": "type",    "target": "Search",   "text": "FS: Q11" }
  { "op": "wait",    "ms": 1500 }
  { "op": "done",    "verdict": "pass" }
  { "op": "fail",    "reason": "Login dialog never appeared" }

``target`` is a natural label (button text, link text) — the executor's
locator ladder handles the actual DOM resolution.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.config import get_settings
from app.executors.web import WebExecutor

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a UI test automation agent driving the OnBase Web Client.

You receive ONE screenshot of the current browser state plus a single test step
to accomplish. Reply with EXACTLY ONE JSON object describing the next atomic
UI action to perform. No prose, no markdown, no code fences — just the JSON.

Allowed shapes:
  {"op":"click","target":"<visible label or selector>"}
  {"op":"type","target":"<input label or selector>","text":"<value>"}
  {"op":"wait","ms":<milliseconds, <= 5000>}
  {"op":"done","verdict":"pass"}
  {"op":"fail","reason":"<short reason>"}

Rules:
- Prefer human-readable labels in "target" (button text, link text, menu name).
- One action per turn. Do not chain.
- Emit "done" only when the step's expected result is visibly satisfied.
- Emit "fail" only when the step is impossible from the current state.
- Never invent passwords or credentials.
- NEVER click "Logout", "Sign Out", "Exit", "Close", or any item inside the
  user account menu in the top-right. The user is already logged in.
- If the page shows a user-account dropdown is open, click outside it first.

OnBase Web layout hints (very important):
- The default page after login is "Document Retrieval".
- To navigate to another module, click the grid/hamburger "Main Menu" button
  in the top-left of the header. The side navigation panel opens listing
  sections: Document, Workflow, Workflow Approval Management, WorkView, etc.
- To open Workflow, after the side panel opens click the text "Open Workflow"
  (it is the link under the "Workflow" heading). It opens in a NEW WINDOW —
  the framework will automatically switch focus to that new window.
- Inside Workflow, lifecycles appear in a tree on the left. Expand a node by
  clicking its name; queues appear nested under it.
- For tree/menu items, prefer clicking the item text itself.
"""


@dataclass
class StepRunResult:
    verdict: str  # "pass" | "fail" | "stuck"
    reason: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)


def _vision_chat() -> ChatOpenAI:
    s = get_settings()
    if not s.github_token:
        raise RuntimeError("GITHUB_TOKEN not set — vision loop requires LLM access.")
    return ChatOpenAI(
        model=s.action_loop_model,
        api_key=s.github_token,
        base_url=s.github_models_endpoint,
        temperature=0.0,
        max_tokens=200,
    )


def _parse_action(text: str) -> dict[str, Any] | None:
    """Extract the first JSON object from the LLM reply."""
    if not text:
        return None
    # strip code fences if present
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    # find first balanced {...}
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


async def run_step(
    executor: WebExecutor,
    step: dict[str, Any],
    *,
    max_iters: int | None = None,
) -> StepRunResult:
    """Drive one Xray step. Returns a verdict + the action history."""
    s = get_settings()
    budget = max_iters or s.action_loop_max_steps
    llm = _vision_chat()
    history: list[dict[str, Any]] = []
    action_text = step.get("action", "") or ""
    expected = step.get("expected", "") or ""

    for it in range(1, budget + 1):
        # Prefer a higher-quality screenshot for the LLM; fall back to stream.
        try:
            jpg = await executor.hi_screenshot()  # type: ignore[attr-defined]
        except AttributeError:
            jpg = await executor.stream_frame()
        b64 = base64.b64encode(jpg).decode("ascii")
        recent = json.dumps(history[-4:], ensure_ascii=False) if history else "[]"
        user_msg = HumanMessage(
            content=[
                {
                    "type": "text",
                    "text": (
                        f"Step: {action_text}\n"
                        f"Expected: {expected or '(not specified)'}\n"
                        f"Iteration: {it}/{budget}\n"
                        f"Recent actions: {recent}\n"
                        "Reply with ONE JSON action."
                    ),
                },
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                },
            ]
        )
        try:
            resp = await llm.ainvoke([SystemMessage(content=SYSTEM_PROMPT), user_msg])
        except Exception as e:
            log.exception("LLM call failed")
            return StepRunResult(verdict="stuck", reason=f"LLM error: {e!r}", history=history)

        raw = (resp.content if isinstance(resp.content, str) else str(resp.content)).strip()
        action = _parse_action(raw)
        if not action:
            history.append({"iter": it, "raw": raw[:200], "parsed": None})
            continue
        op = (action.get("op") or "").lower()

        if op == "done":
            history.append({"iter": it, "action": action})
            return StepRunResult(verdict="pass", history=history)
        if op == "fail":
            history.append({"iter": it, "action": action})
            return StepRunResult(
                verdict="fail",
                reason=action.get("reason", "(no reason)"),
                history=history,
            )
        if op == "wait":
            ms = int(action.get("ms", 1000))
            await _async_sleep_ms(min(max(ms, 100), 5000))
            history.append({"iter": it, "action": action, "result": "waited"})
            continue
        if op == "click":
            target = action.get("target", "")
            res = await executor.click(target)
            history.append({"iter": it, "action": action, "ok": res.ok, "detail": res.detail})
            continue
        if op == "type":
            target = action.get("target", "")
            text = action.get("text", "")
            res = await executor.type_text(target, text)
            history.append({"iter": it, "action": action, "ok": res.ok, "detail": res.detail})
            continue
        # unknown op
        history.append({"iter": it, "action": action, "error": "unknown op"})

    return StepRunResult(verdict="stuck", reason=f"budget {budget} exhausted", history=history)


async def _async_sleep_ms(ms: int) -> None:
    import asyncio

    await asyncio.sleep(ms / 1000.0)
