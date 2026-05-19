"""Phase 1 "RAG-grounded" chat graph.

A single LLM node, but each turn first retrieves top-k chunks from the regression
corpus (Confluence + MRG + local docs + past run-logs) and injects them as a
labeled context block. The model is instructed to ground its answer in those
chunks and cite them. The actual citation list is also surfaced to the UI as
clickable chips.

Phase 2 will replace this single node with the real Drive FSM (CONFIG ->
PREFLIGHT -> FETCH-TEST -> SHOW-STEP -> AWAIT-VERDICT -> BUG-DRAFT -> APPROVAL
-> POSTED).
"""
from __future__ import annotations

import logging
from typing import Annotated, AsyncIterator, TypedDict

from langchain_core.messages import (
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

from app.config import get_settings
from app.llm import get_chat_model
from app.rag.retriever import RetrievedChunk, format_context, retrieve

log = logging.getLogger(__name__)


SYSTEM_PROMPT = (
    "You are the SBPPA Regression Agent for the OnBase Workflow + Document "
    "Composition team, supporting the 26.1 regression cycle anchored on Jira "
    "Xray Test Plan SBPPA-14690 (\"26.1 - Regression - Workflow\").\n\n"
    "GROUNDING RULES (strict):\n"
    "1. When a CONTEXT block is provided below, treat it as the source of truth. "
    "Prefer it over your own training knowledge whenever they conflict.\n"
    "2. Cite the doc number for every factual claim you take from CONTEXT, like "
    "[doc 1] or [doc 2]. Multiple cites allowed: [doc 1][doc 3].\n"
    "3. If CONTEXT does not contain the answer, say so explicitly: "
    "\"I don't have that in the indexed corpus.\" Do NOT invent ticket IDs, "
    "label names, fixVersions, naming patterns, or process steps.\n"
    "4. If the user asks for live Jira/Xray data (counts, who's executing, "
    "today's status), say it's not wired yet (Phase 2) and offer to answer from "
    "the corpus instead.\n\n"
    "STYLE: concise. Bullets over prose. No filler. No apologies."
)


class GraphState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


def _build_messages(
    user_text: str, history: list[BaseMessage], chunks: list[RetrievedChunk]
) -> list[BaseMessage]:
    msgs: list[BaseMessage] = [SystemMessage(content=SYSTEM_PROMPT)]
    if chunks:
        ctx = format_context(chunks)
        msgs.append(
            SystemMessage(
                content=(
                    "CONTEXT — retrieved from the regression corpus. "
                    "Cite by [doc N].\n\n" + ctx
                )
            )
        )
    msgs.extend(history)
    msgs.append(HumanMessage(content=user_text))
    return msgs


async def stream_reply(
    user_text: str, history: list[BaseMessage] | None = None
) -> AsyncIterator[dict]:
    """Stream the assistant's reply for one user turn.

    Yields dicts of one of these shapes:
        {"type": "sources", "items": [ {source, path, title, score}, ... ]}
        {"type": "token", "text": "..."}
    """
    s = get_settings()
    try:
        raw_chunks = retrieve(user_text, k=s.rag_top_k)
    except Exception as exc:  # noqa: BLE001  Qdrant down or empty collection
        log.warning("retrieval failed (%s); proceeding without context", exc)
        raw_chunks = []

    chunks = [c for c in raw_chunks if c.score >= s.rag_min_score]

    yield {
        "type": "sources",
        "items": [
            {
                "n": i + 1,
                "source": c.source,
                "path": c.path,
                "title": c.title,
                "score": round(c.score, 3),
            }
            for i, c in enumerate(chunks)
        ],
    }

    model = get_chat_model()
    messages = _build_messages(user_text, history or [], chunks)

    async for chunk in model.astream(messages):
        if isinstance(chunk, AIMessageChunk) and chunk.content:
            text = chunk.content if isinstance(chunk.content, str) else str(chunk.content)
            yield {"type": "token", "text": text}


def build_graph():
    """Compiled LangGraph kept for parity with Phase 2 — currently a single
    pass-through node. The real streaming happens in `stream_reply`."""

    async def _llm_node(state: GraphState) -> dict:
        model = get_chat_model()
        response = await model.ainvoke([SystemMessage(content=SYSTEM_PROMPT)] + state["messages"])
        return {"messages": [response]}

    g = StateGraph(GraphState)
    g.add_node("llm", _llm_node)
    g.set_entry_point("llm")
    g.add_edge("llm", END)
    return g.compile()

