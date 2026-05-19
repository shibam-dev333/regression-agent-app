"""Text chunking. Markdown-aware split with reasonable defaults.

Chunks aim at ~600 tokens (roughly 2400 chars) with 80-char overlap, which
keeps headings + their following paragraphs in the same chunk most of the time
while staying well under any model's context budget.
"""
from __future__ import annotations

from langchain_text_splitters import RecursiveCharacterTextSplitter

CHUNK_SIZE = 2400
CHUNK_OVERLAP = 240

_SPLITTER = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE,
    chunk_overlap=CHUNK_OVERLAP,
    separators=["\n## ", "\n### ", "\n\n", "\n", " ", ""],
    length_function=len,
)


def chunk_text(text: str) -> list[str]:
    return _SPLITTER.split_text(text)
