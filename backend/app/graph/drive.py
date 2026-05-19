"""Phase 0 "hello" LangGraph.

A single node calls the LLM and streams tokens back. Phase 2 replaces this with
the real Drive FSM (CONFIG -> PREFLIGHT -> FETCH-TEST -> SHOW-STEP ->
AWAIT-VERDICT -> BUG-DRAFT -> APPROVAL -> POSTED).
"""
from __future__ import annotations

from typing import Annotated, AsyncIterator, TypedDict

from langchain_core.messages import AIMessageChunk, BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

from app.llm import get_chat_model


SYSTEM_PROMPT = (
    "You are the SBPPA Regression Agent (Phase 0 — scaffold). "
    "The full Drive state machine and compliance gate are not wired yet. "
    "For now, answer regression-testing questions about OnBase 26.1 concisely, "
    "and remind the user the production loop (drive / log bug / status) goes "
    "live in Phase 2."
)


class GraphState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


async def _llm_node(state: GraphState) -> dict:
    """Call the LLM with the conversation so far. Streaming happens in the runner."""
    model = get_chat_model()
    messages: list[BaseMessage] = [SystemMessage(content=SYSTEM_PROMPT)] + state["messages"]
    response = await model.ainvoke(messages)
    return {"messages": [response]}


def build_graph():
    g = StateGraph(GraphState)
    g.add_node("llm", _llm_node)
    g.set_entry_point("llm")
    g.add_edge("llm", END)
    return g.compile()


_graph = build_graph()


async def stream_reply(user_text: str, history: list[BaseMessage] | None = None) -> AsyncIterator[str]:
    """Stream the LLM's reply for one user turn, token by token."""
    model = get_chat_model()
    messages: list[BaseMessage] = [SystemMessage(content=SYSTEM_PROMPT)]
    if history:
        messages.extend(history)
    messages.append(HumanMessage(content=user_text))
    async for chunk in model.astream(messages):
        if isinstance(chunk, AIMessageChunk) and chunk.content:
            yield chunk.content if isinstance(chunk.content, str) else str(chunk.content)
