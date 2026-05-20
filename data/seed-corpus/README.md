# Seed corpus

Anything you drop in this folder (`.md`, `.markdown`, `.txt`, `.mdx`, `.rst`,
`.html`, `.htm`) gets indexed into the RAG vector store the next time you run:

```
cd backend
uv run python -m app.rag.ingest_cli seed
```

This folder is the **default** seed source — see `seed-sources.toml` at the
repo root to add more (Confluence exports, MRG dumps, sibling repos, etc.).
Re-runs are idempotent (chunk IDs are stable), so you can re-ingest as often
as you like without creating duplicates.

Citations from these files show up in the chat UI as `[N] <title>` chips
labeled `app-docs`.
