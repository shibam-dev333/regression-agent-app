"""WebSocket chat endpoint. Wire protocol (JSON, one message per frame):

Client -> server:
    { "type": "user", "text": "...", "history": [ {"role":"user|assistant","text":"..."} ] }

Server -> client (multiple frames per turn):
    { "type": "sources", "items": [ {n, source, path, title, score}, ... ] }
    { "type": "token", "text": "..." }   # streamed token
    { "type": "done" }                   # end of assistant turn
    { "type": "error", "text": "..." }   # fatal turn error
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from app.graph.drive import stream_reply

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
    try:
        while True:
            raw = await ws.receive_text()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_text(json.dumps({"type": "error", "text": "invalid JSON"}))
                continue

            if payload.get("type") != "user":
                await ws.send_text(
                    json.dumps({"type": "error", "text": "expected type=user"})
                )
                continue

            user_text = (payload.get("text") or "").strip()
            if not user_text:
                await ws.send_text(json.dumps({"type": "done"}))
                continue

            history = _decode_history(payload.get("history"))

            try:
                async for event in stream_reply(user_text, history):
                    await ws.send_text(json.dumps(event))
                await ws.send_text(json.dumps({"type": "done"}))
            except Exception as exc:  # noqa: BLE001  — surface any runtime error to the client
                log.exception("chat turn failed")
                await ws.send_text(json.dumps({"type": "error", "text": str(exc)}))

    except WebSocketDisconnect:
        return
