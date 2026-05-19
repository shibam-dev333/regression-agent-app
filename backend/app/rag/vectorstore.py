"""Qdrant client + collection management.

One collection per environment. Documents carry metadata: `source` (kind),
`path` (origin URL or file path), `title`, `chunk_index`, `ingested_at`.
"""
from __future__ import annotations

from functools import lru_cache

from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from app.config import get_settings
from app.rag.embeddings import EMBED_DIM, get_embeddings

COLLECTION_NAME = "regression_corpus"


@lru_cache
def get_qdrant_client() -> QdrantClient:
    s = get_settings()
    return QdrantClient(url=s.qdrant_url)


def ensure_collection() -> None:
    client = get_qdrant_client()
    existing = {c.name for c in client.get_collections().collections}
    if COLLECTION_NAME in existing:
        return
    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=qmodels.VectorParams(size=EMBED_DIM, distance=qmodels.Distance.COSINE),
    )


@lru_cache
def get_vectorstore() -> QdrantVectorStore:
    ensure_collection()
    return QdrantVectorStore(
        client=get_qdrant_client(),
        collection_name=COLLECTION_NAME,
        embedding=get_embeddings(),
    )


def collection_stats() -> dict:
    """Return basic stats for the /health endpoint."""
    try:
        client = get_qdrant_client()
        existing = {c.name for c in client.get_collections().collections}
        if COLLECTION_NAME not in existing:
            return {"exists": False, "points": 0}
        info = client.get_collection(COLLECTION_NAME)
        return {"exists": True, "points": info.points_count or 0}
    except Exception as exc:  # noqa: BLE001
        return {"exists": False, "error": str(exc)}
