"""Local embeddings.

We use a small sentence-transformer running on CPU. Rationale:

- The user's GitHub Models token is blocked from `openai/*` (which includes
  `openai/text-embedding-3-small`), so paid hosted embeddings aren't an option.
- Local embeddings are free, fast on CPU, deterministic, and never leave the
  laptop — appropriate for SBPPA's MRG and internal Confluence pages.

`BAAI/bge-small-en-v1.5` is 384-dim, ~33M params, ranks well on MTEB for the
size, and downloads once (~130MB) to the HF cache.
"""
from __future__ import annotations

from functools import lru_cache

from langchain_huggingface import HuggingFaceEmbeddings

EMBED_MODEL_NAME = "BAAI/bge-small-en-v1.5"
EMBED_DIM = 384


@lru_cache
def get_embeddings() -> HuggingFaceEmbeddings:
    return HuggingFaceEmbeddings(
        model_name=EMBED_MODEL_NAME,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )
