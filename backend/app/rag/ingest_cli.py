"""CLI for corpus ingestion.

Usage:

    uv run python -m app.rag.ingest_cli local --path c:\\26.1_Onbase\\.github\\agents
    uv run python -m app.rag.ingest_cli local --path c:\\regression-agent-app\\data\\seed-corpus
    uv run python -m app.rag.ingest_cli confluence
    uv run python -m app.rag.ingest_cli mrg
    uv run python -m app.rag.ingest_cli stats
    uv run python -m app.rag.ingest_cli reset       # drop the collection (destructive)
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import typer

from app.rag.ingest.confluence import ingest_confluence
from app.rag.ingest.local_docs import ingest_folder
from app.rag.ingest.mrg import ingest_mrg
from app.rag.vectorstore import (
    COLLECTION_NAME,
    collection_stats,
    ensure_collection,
    get_qdrant_client,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.command("local")
def cmd_local(
    path: str = typer.Option(..., "--path", "-p", help="Folder to ingest"),
    label: str = typer.Option("local", "--label", "-l", help="source label for metadata"),
):
    """Ingest every .md/.txt/.mdx under PATH."""
    result = ingest_folder(Path(path), source_label=label)
    typer.echo(json.dumps(result, indent=2))


@app.command("confluence")
def cmd_confluence():
    """Ingest configured Confluence spaces."""
    result = ingest_confluence()
    typer.echo(json.dumps(result, indent=2))


@app.command("mrg")
def cmd_mrg():
    """Ingest the MRG_DATA_DIR folder."""
    result = ingest_mrg()
    typer.echo(json.dumps(result, indent=2))


@app.command("stats")
def cmd_stats():
    """Show collection size."""
    ensure_collection()
    typer.echo(json.dumps(collection_stats(), indent=2))


@app.command("reset")
def cmd_reset(
    yes: bool = typer.Option(False, "--yes", help="Confirm destructive drop"),
):
    """Drop and recreate the collection."""
    if not yes:
        typer.echo("Refusing to drop without --yes")
        raise typer.Exit(code=1)
    client = get_qdrant_client()
    existing = {c.name for c in client.get_collections().collections}
    if COLLECTION_NAME in existing:
        client.delete_collection(COLLECTION_NAME)
    ensure_collection()
    typer.echo(json.dumps({"reset": True, "collection": COLLECTION_NAME}, indent=2))


if __name__ == "__main__":
    app()
