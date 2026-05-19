"""Ingest local markdown / text files from a folder tree.

Walks PATH recursively, reads every `.md`, `.markdown`, `.txt`, and `.mdx`
file, chunks it, embeds it, upserts into Qdrant with metadata pointing back to
the file path (so citations are clickable in tools that link file paths).
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from langchain_core.documents import Document

from app.rag.chunker import chunk_text
from app.rag.vectorstore import ensure_collection, get_vectorstore

log = logging.getLogger(__name__)

ALLOWED_SUFFIXES = {".md", ".markdown", ".txt", ".mdx", ".rst"}


def _iter_files(root: Path) -> Iterable[Path]:
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in ALLOWED_SUFFIXES:
            continue
        # skip hidden dirs (.git, .next, .venv)
        if any(part.startswith(".") and part not in {".github"} for part in p.parts):
            continue
        if any(part in {"node_modules", "__pycache__", "dist", "build"} for part in p.parts):
            continue
        yield p


def _stable_id(path: Path, chunk_index: int) -> str:
    h = hashlib.sha1(f"{path.as_posix()}::{chunk_index}".encode("utf-8")).hexdigest()
    # Qdrant accepts UUIDs or unsigned ints as point ids; use uuid-shaped sha1
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def ingest_folder(root: Path, source_label: str = "local") -> dict:
    """Ingest every supported file under `root`. Returns counts."""
    ensure_collection()
    vs = get_vectorstore()

    root = root.resolve()
    if not root.exists():
        raise FileNotFoundError(f"Path not found: {root}")

    docs: list[Document] = []
    ids: list[str] = []
    files_seen = 0
    chunks_emitted = 0
    now = datetime.now(timezone.utc).isoformat()

    for fp in _iter_files(root):
        files_seen += 1
        try:
            text = fp.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = fp.read_text(encoding="utf-8", errors="ignore")
        if not text.strip():
            continue

        title = fp.stem.replace("-", " ").replace("_", " ")
        rel = fp.relative_to(root).as_posix()

        for i, chunk in enumerate(chunk_text(text)):
            docs.append(
                Document(
                    page_content=chunk,
                    metadata={
                        "source": source_label,
                        "path": fp.as_posix(),
                        "rel_path": rel,
                        "title": title,
                        "chunk_index": i,
                        "ingested_at": now,
                    },
                )
            )
            ids.append(_stable_id(fp, i))
            chunks_emitted += 1

    if docs:
        vs.add_documents(documents=docs, ids=ids)

    log.info(
        "local_docs ingest: root=%s files=%d chunks=%d", root, files_seen, chunks_emitted
    )
    return {
        "source": source_label,
        "root": str(root),
        "files": files_seen,
        "chunks": chunks_emitted,
    }
