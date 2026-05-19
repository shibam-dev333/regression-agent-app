"""Confluence ingest — STUB.

To enable, fill in these env vars in `.env`:

    CONFLUENCE_BASE_URL=https://hyland.atlassian.net/wiki
    CONFLUENCE_EMAIL=you@hyland.com
    CONFLUENCE_API_TOKEN=<a Confluence API token from https://id.atlassian.com/manage-profile/security/api-tokens>
    CONFLUENCE_SPACE_KEYS=SBPPA,QUAL,OB26       # comma-separated

Then run:

    uv run python -m app.rag.ingest_cli --source confluence

This module uses the `atlassian-python-api` package's `Confluence` client to
page through each configured space, strip the storage-format HTML to plain
text, chunk, and upsert.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable

from bs4 import BeautifulSoup
from langchain_core.documents import Document

from app.config import get_settings
from app.rag.chunker import chunk_text
from app.rag.vectorstore import ensure_collection, get_vectorstore

log = logging.getLogger(__name__)


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    for bad in soup(["script", "style"]):
        bad.decompose()
    return soup.get_text(separator="\n").strip()


def _iter_space_pages(client, space_key: str, batch: int = 50) -> Iterable[dict]:
    start = 0
    while True:
        page_batch = client.get_all_pages_from_space(
            space=space_key,
            start=start,
            limit=batch,
            expand="body.storage,version",
        )
        if not page_batch:
            return
        for p in page_batch:
            yield p
        if len(page_batch) < batch:
            return
        start += batch


def ingest_confluence() -> dict:
    s = get_settings()
    if not (s.confluence_base_url and s.confluence_email and s.confluence_api_token):
        raise RuntimeError(
            "Confluence credentials missing. Set CONFLUENCE_BASE_URL, "
            "CONFLUENCE_EMAIL, CONFLUENCE_API_TOKEN in .env."
        )
    if not s.confluence_space_keys_list:
        raise RuntimeError(
            "No Confluence spaces configured. Set CONFLUENCE_SPACE_KEYS in .env "
            "to a comma-separated list of space keys (e.g. SBPPA,QUAL,OB26)."
        )

    # Lazy import so the package isn't required unless the user actually uses it
    from atlassian import Confluence  # type: ignore

    client = Confluence(
        url=s.confluence_base_url,
        username=s.confluence_email,
        password=s.confluence_api_token,
        cloud=True,
    )

    ensure_collection()
    vs = get_vectorstore()

    docs: list[Document] = []
    pages_seen = 0
    chunks_emitted = 0
    now = datetime.now(timezone.utc).isoformat()

    for space_key in s.confluence_space_keys_list:
        log.info("ingesting Confluence space: %s", space_key)
        for page in _iter_space_pages(client, space_key):
            pages_seen += 1
            body_html = (((page.get("body") or {}).get("storage") or {}).get("value")) or ""
            text = _html_to_text(body_html)
            if not text:
                continue
            title = page.get("title", "untitled")
            page_id = page.get("id", "")
            page_url = f"{s.confluence_base_url}/spaces/{space_key}/pages/{page_id}"
            for i, chunk in enumerate(chunk_text(text)):
                docs.append(
                    Document(
                        page_content=chunk,
                        metadata={
                            "source": "confluence",
                            "path": page_url,
                            "space_key": space_key,
                            "page_id": page_id,
                            "title": title,
                            "chunk_index": i,
                            "ingested_at": now,
                        },
                    )
                )
                chunks_emitted += 1

    if docs:
        vs.add_documents(documents=docs)

    log.info("confluence ingest: pages=%d chunks=%d", pages_seen, chunks_emitted)
    return {"source": "confluence", "pages": pages_seen, "chunks": chunks_emitted}
