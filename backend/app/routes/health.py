from __future__ import annotations

from fastapi import APIRouter

from app.config import get_settings

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    s = get_settings()
    return {
        "status": "ok",
        "phase": 0,
        "llm_configured": bool(s.github_token),
        "model": s.github_models_chat_model,
    }
