"""WebSocket chat endpoint. Wire protocol (JSON, one message per frame):

Client -> server:
    { "type": "user", "text": "...", "history": [ {"role":"user|assistant","text":"..."} ] }

Server -> client (multiple frames per turn):
    { "type": "sources", "items": [ {n, source, path, title, score}, ... ] }
    { "type": "token", "text": "..." }                # streamed token
    { "type": "status", "text": "..." }               # drive runner progress
    { "type": "issue", key, summary, issue_type, status, url }
    { "type": "tests", exec_key, tests: [{key,title}] }
    { "type": "steps", test_key, count }
    { "type": "route", client, confidence, reason }
    { "type": "step", idx, total, action, data, expected }
    { "type": "frame", client, jpg_b64 }              # live view JPEG
    { "type": "done" }                                # end of assistant turn
    { "type": "error", "text": "..." }   # fatal turn error
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from app.graph.drive import stream_reply
from app.graph.drive_runner import DriveSession, is_drive_command

router = APIRouter()
log = logging.getLogger(__name__)


def _decode_history(raw: list[dict] | None) -> list[BaseMessage]:
    if not raw:
        return []
    out: list[BaseMessage] = []
    for m in raw:
        role = m.get("role")
        text = m.get("text", "")
        if not text:
            continue
        if role == "user":
            out.append(HumanMessage(content=text))
        elif role == "assistant":
            out.append(AIMessage(content=text))
    return out


@router.websocket("/api/chat")
async def chat_ws(ws: WebSocket) -> None:
    await ws.accept()

    async def emit(event: dict) -> None:
        await ws.send_text(json.dumps(event))

    drive = DriveSession(emit=emit)

    try:
        while True:
            raw = await ws.receive_text()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                await emit({"type": "error", "text": "invalid JSON"})
                continue

            if payload.get("type") != "user":
                await emit({"type": "error", "text": "expected type=user"})
                continue

            user_text = (payload.get("text") or "").strip()
            if not user_text:
                await emit({"type": "done"})
                continue

            # Drive commands (run X, stop, and any reply while a session is
            # mid-flow — pick / verdict / use client) take priority over RAG.
            if is_drive_command(user_text, drive):
                try:
                    handled = await drive.handle_command(user_text)
                except Exception as exc:  # noqa: BLE001
                    log.exception("drive command failed")
                    await emit({"type": "error", "text": str(exc)})
                    handled = True
                if handled:
                    await emit({"type": "done"})
                    continue

            history = _decode_history(payload.get("history"))
            try:
                async for event in stream_reply(user_text, history):
                    await emit(event)
                await emit({"type": "done"})
            except Exception as exc:  # noqa: BLE001
                log.exception("chat turn failed")
                await emit({"type": "error", "text": str(exc)})

    except WebSocketDisconnect:
        await drive.stop()
        return
