"""GitHub Models client. OpenAI-compatible — uses langchain-openai under the hood."""
from __future__ import annotations

from functools import lru_cache

from langchain_openai import ChatOpenAI

from app.config import get_settings


@lru_cache
def get_chat_model() -> ChatOpenAI:
    """A streaming chat model backed by GitHub Models.

    Auth: bearer GITHUB_TOKEN (PAT with `models:read`).
    Endpoint is OpenAI-compatible at github_models_endpoint.
    """
    s = get_settings()
    if not s.github_token:
        raise RuntimeError(
            "GITHUB_TOKEN is not set. Copy .env.example to .env and fill it in."
        )
    return ChatOpenAI(
        model=s.github_models_chat_model,
        api_key=s.github_token,
        base_url=s.github_models_endpoint,
        streaming=True,
        temperature=0.2,
    )
