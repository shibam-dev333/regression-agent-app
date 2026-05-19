from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    github_token: str = ""
    github_models_endpoint: str = "https://models.github.ai/inference"
    github_models_chat_model: str = "openai/gpt-4o-mini"
    github_models_embed_model: str = "openai/text-embedding-3-small"

    backend_host: str = "0.0.0.0"
    backend_port: int = 8000
    backend_cors_origins: str = "http://localhost:3000"

    # ── RAG ──────────────────────────────────────────────────────────────────
    qdrant_url: str = "http://localhost:6333"
    rag_top_k: int = 5
    rag_min_score: float = 0.30  # below this we drop the chunk (cosine similarity)

    # ── Confluence (stub for now) ────────────────────────────────────────────
    confluence_base_url: str = ""
    confluence_email: str = ""
    confluence_api_token: str = ""
    confluence_space_keys: str = ""  # comma-separated: "SBPPA,QUAL,OB26"

    # ── MRG (stub for now) ───────────────────────────────────────────────────
    mrg_data_dir: str = ""

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.backend_cors_origins.split(",") if o.strip()]

    @property
    def confluence_space_keys_list(self) -> list[str]:
        return [k.strip() for k in self.confluence_space_keys.split(",") if k.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
