"""MRG ingest — STUB.

OnBase MRG has no public API. Two strategies, pick whichever is cheaper for you:

Strategy A (recommended): export MRG entries for the Workflow + DocComp modules
to a folder (one .md/.html/.txt per entry, or a single CSV). Set MRG_DATA_DIR in
.env. This module then ingests that folder. You re-export periodically.

Strategy B: authenticated HTTP scrape against the MRG web UI. Requires a session
cookie or basic-auth header — write that into `_fetch_mrg_html()` when ready.

For now this just delegates to local_docs against MRG_DATA_DIR if it's set.
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.config import get_settings
from app.rag.ingest.local_docs import ingest_folder

log = logging.getLogger(__name__)


def ingest_mrg() -> dict:
    s = get_settings()
    if not s.mrg_data_dir:
        raise RuntimeError(
            "MRG_DATA_DIR is not set. Export MRG entries to a folder and set "
            "MRG_DATA_DIR=<path> in .env."
        )
    root = Path(s.mrg_data_dir)
    if not root.exists():
        raise FileNotFoundError(f"MRG_DATA_DIR does not exist: {root}")
    return ingest_folder(root, source_label="mrg")
