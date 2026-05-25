"""CLI for the Jira + Xray scraper.

    uv run python -m app.tools.scraper_cli login                # one-time SSO, saves cookies
    uv run python -m app.tools.scraper_cli issue SBPPA-14878    # REST fetch
    uv run python -m app.tools.scraper_cli exec SBPPA-14878     # Xray scrape: tests in execution
    uv run python -m app.tools.scraper_cli steps SBPPA-XXXXX    # Xray scrape: test steps
"""
from __future__ import annotations

import asyncio
import json
import logging

import typer

from app.tools.jira_scraper import JiraClient, XrayScraper

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.command("login")
def cmd_login():
    """Open a headed browser, complete SSO, save cookies."""

    async def _run():
        async with XrayScraper(headless=False) as s:
            await s.login_interactive()

    asyncio.run(_run())


@app.command("issue")
def cmd_issue(key: str):
    """REST fetch a Jira issue by key."""

    async def _run():
        c = JiraClient()
        out = await c.fetch_issue(key)
        typer.echo(json.dumps(out, indent=2, default=str))

    asyncio.run(_run())


@app.command("exec")
def cmd_exec(key: str, headless: bool = typer.Option(True, "--headless/--headed")):
    """Scrape the Tests panel of a Test Execution issue."""

    async def _run():
        async with XrayScraper(headless=headless) as s:
            out = await s.fetch_execution_tests(key)
            typer.echo(json.dumps(out, indent=2))

    asyncio.run(_run())


@app.command("steps")
def cmd_steps(key: str, headless: bool = typer.Option(True, "--headless/--headed")):
    """Scrape action/data/expected steps from a Test issue."""

    async def _run():
        async with XrayScraper(headless=headless) as s:
            out = await s.fetch_xray_test_steps(key)
            typer.echo(json.dumps(out, indent=2))

    asyncio.run(_run())


if __name__ == "__main__":
    app()
