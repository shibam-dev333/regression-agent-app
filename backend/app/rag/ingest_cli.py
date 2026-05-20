"""CLI for corpus ingestion.

Usage:

    uv run python -m app.rag.ingest_cli seed                # walk seed-sources.toml
    uv run python -m app.rag.ingest_cli local --path ./docs
    uv run python -m app.rag.ingest_cli confluence
    uv run python -m app.rag.ingest_cli mrg
    uv run python -m app.rag.ingest_cli stats
    uv run python -m app.rag.ingest_cli reset --yes         # drop collection
"""
from __future__ import annotations

import json
import logging
import tomllib
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

# Repo root = two parents up from this file: backend/app/rag/ingest_cli.py -> repo
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SEED_CONFIG = REPO_ROOT / "seed-sources.toml"

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.command("local")
def cmd_local(
    path: str = typer.Option(..., "--path", "-p", help="Folder to ingest"),
    label: str = typer.Option("local", "--label", "-l", help="source label for metadata"),
):
    """Ingest every supported file under PATH."""
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


def _resolve_path(raw: str) -> Path:
    p = Path(raw)
    if not p.is_absolute():
        p = (REPO_ROOT / p).resolve()
    return p


def _load_seed_config(config_path: Path) -> list[dict]:
    if not config_path.exists():
        raise FileNotFoundError(
            f"Seed config not found: {config_path}. "
            "Pass --config to point at a different TOML."
        )
    with config_path.open("rb") as f:
        data = tomllib.load(f)
    sources = data.get("source", [])
    if not isinstance(sources, list):
        raise ValueError(f"{config_path}: [[source]] must be a list of tables")
    return sources


@app.command("seed")
def cmd_seed(
    config: str = typer.Option(
        str(DEFAULT_SEED_CONFIG),
        "--config",
        "-c",
        help="Path to seed-sources.toml. Defaults to <repo-root>/seed-sources.toml.",
    ),
):
    """Ingest every enabled entry in seed-sources.toml.

    The config is checked into the repo, so a fresh clone gets a working command
    out of the box. Per-machine paths (sibling repos, exported corpora) can be
    enabled or remapped without code changes.
    """
    cfg_path = Path(config).resolve()
    sources = _load_seed_config(cfg_path)

    results: list[dict] = []
    skipped: list[dict] = []
    for entry in sources:
        label = entry.get("label")
        raw_path = entry.get("path")
        enabled = entry.get("enabled", True)

        if not label or not raw_path:
            typer.echo(f"[bad-entry] missing path/label: {entry}", err=True)
            continue
        if not enabled:
            skipped.append({"label": label, "path": raw_path, "reason": "disabled"})
            continue

        p = _resolve_path(raw_path)
        if not p.exists():
            skipped.append({"label": label, "path": str(p), "reason": "not found"})
            typer.echo(f"[skip] {label}: {p} not found", err=True)
            continue

        typer.echo(f"[ingest] {label}: {p}")
        results.append(ingest_folder(p, source_label=label))

    summary = {
        "config": str(cfg_path),
        "ingested": results,
        "skipped": skipped,
        "collection": collection_stats(),
    }
    typer.echo(json.dumps(summary, indent=2))


if __name__ == "__main__":
    app()
