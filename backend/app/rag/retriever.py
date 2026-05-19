"""Retrieval: similarity search returning chunks + metadata for citation."""
from __future__ import annotations

from dataclasses import dataclass

from app.rag.vectorstore import get_vectorstore


@dataclass
class RetrievedChunk:
    text: str
    source: str           # "local" | "confluence" | "mrg" | "jira"
    path: str             # file path or URL
    title: str
    score: float

    def citation(self) -> str:
        return f"[{self.source}:{self.title}]"


def retrieve(query: str, k: int = 5) -> list[RetrievedChunk]:
    """Top-k similar chunks for the user query."""
    vs = get_vectorstore()
    hits = vs.similarity_search_with_score(query, k=k)
    out: list[RetrievedChunk] = []
    for doc, score in hits:
        md = doc.metadata or {}
        out.append(
            RetrievedChunk(
                text=doc.page_content,
                source=str(md.get("source", "unknown")),
                path=str(md.get("path", "")),
                title=str(md.get("title", md.get("path", "untitled"))),
                score=float(score),
            )
        )
    return out


def format_context(chunks: list[RetrievedChunk]) -> str:
    """Render retrieved chunks as a labeled block to inject into the LLM prompt."""
    if not chunks:
        return ""
    blocks = []
    for i, c in enumerate(chunks, 1):
        blocks.append(
            f"[doc {i} | source={c.source} | title={c.title} | path={c.path}]\n{c.text}"
        )
    return "\n\n---\n\n".join(blocks)
