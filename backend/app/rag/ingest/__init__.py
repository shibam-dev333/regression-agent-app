"""Ingestion package. One module per source.

Sources today:
- `local_docs`  — read markdown/text from a folder (seed corpus, sibling agent file, run-logs)

Sources stubbed (TODO when creds/access available):
- `confluence`  — Atlassian REST API per-space ingest
- `mrg`         — MRG export folder watcher

Run via `python -m app.rag.ingest_cli`.
"""
