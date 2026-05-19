from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routes import chat, health

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")

settings = get_settings()

app = FastAPI(
    title="Regression Agent Backend",
    version="0.1.0",
    description="Phase 0 scaffold — FastAPI + LangGraph + GitHub Models.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(chat.router)


@app.get("/")
async def root() -> dict:
    return {
        "name": "regression-agent-backend",
        "phase": 0,
        "endpoints": {
            "health": "/health",
            "chat_ws": "/api/chat",
            "docs": "/docs",
        },
    }
