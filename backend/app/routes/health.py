from __future__ import annotations

from fastapi import APIRouter

from app.config import get_settings
from app.rag.vectorstore import collection_stats

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    s = get_settings()
    return {
        "status": "ok",
        "phase": 1,
        "llm_configured": bool(s.github_token),
        "model": s.github_models_chat_model,
        "rag": {
            "qdrant_url": s.qdrant_url,
            "top_k": s.rag_top_k,
            "collection": collection_stats(),
        },
        "confluence_configured": bool(
            s.confluence_base_url and s.confluence_email and s.confluence_api_token
        ),
        "mrg_configured": bool(s.mrg_data_dir),
    }
