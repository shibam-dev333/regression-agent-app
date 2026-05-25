"""Jira + Xray scraper.

Two layers:

1. ``JiraClient`` — httpx + Basic auth (email + personal API token).
   Used for everything Jira REST exposes: issue fetch, remote links, JQL.
   No admin required; user generates a token at
   https://id.atlassian.com/manage-profile/security/api-tokens.

2. ``XrayScraper`` — Playwright with persistent storage_state.
   Used only where REST falls short: scraping the Xray Cloud iframe panels
   on a Test or Test Execution for step content + linked test lists.
   First-run is interactive (headed, user does SSO once); subsequent runs
   replay cookies from ``data/storage_state.json``.

Results are cached to ``data/xray-cache/<key>.json`` so repeat fetches during
a regression run are instant and offline-replayable.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = REPO_ROOT / "data"
CACHE_DIR = DATA_DIR / "xray-cache"
STORAGE_STATE = DATA_DIR / "storage_state.json"


# ──────────────────────────────────────────────────────────────────────────────
# JiraClient — REST over personal API token
# ──────────────────────────────────────────────────────────────────────────────


class JiraAuthError(RuntimeError):
    pass


class JiraClient:
    """Thin async wrapper around Jira Cloud REST v3."""

    def __init__(self, base_url: str | None = None, email: str | None = None, token: str | None = None):
        s = get_settings()
        self.base_url = (base_url or s.jira_base_url).rstrip("/")
        self.email = email or s.atlassian_email
        self.token = token or s.atlassian_api_token
        if not (self.email and self.token):
            raise JiraAuthError(
                "ATLASSIAN_EMAIL + ATLASSIAN_API_TOKEN must be set in .env. "
                "Create a token at https://id.atlassian.com/manage-profile/security/api-tokens"
            )

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            auth=(self.email, self.token),
            headers={"Accept": "application/json", "X-Atlassian-Token": "no-check"},
            timeout=30.0,
        )

    async def fetch_issue(self, key: str, fields: list[str] | None = None) -> dict[str, Any]:
        """Return a normalized issue dict. Falls through Jira raw under ``_raw``."""
        params: dict[str, Any] = {}
        if fields:
            params["fields"] = ",".join(fields)
        async with self._client() as c:
            r = await c.get(f"/rest/api/3/issue/{key}", params=params)
            if r.status_code == 401:
                raise JiraAuthError("Jira returned 401 — check ATLASSIAN_EMAIL / ATLASSIAN_API_TOKEN.")
            r.raise_for_status()
            raw = r.json()
        f = raw.get("fields", {}) or {}
        return {
            "key": raw.get("key"),
            "summary": f.get("summary"),
            "issue_type": (f.get("issuetype") or {}).get("name"),
            "status": (f.get("status") or {}).get("name"),
            "assignee": (f.get("assignee") or {}).get("emailAddress"),
            "labels": f.get("labels") or [],
            "fix_versions": [v.get("name") for v in (f.get("fixVersions") or [])],
            "priority": (f.get("priority") or {}).get("name"),
            "project": (f.get("project") or {}).get("key"),
            "description_adf": f.get("description"),  # ADF JSON; renderable later
            "url": f"{self.base_url}/browse/{raw.get('key')}",
            "_raw_fields_keys": sorted(f.keys()),
        }

    async def search(self, jql: str, fields: list[str] | None = None, max_results: int = 50) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"jql": jql, "maxResults": max_results}
        if fields:
            payload["fields"] = fields
        async with self._client() as c:
            r = await c.post("/rest/api/3/search", json=payload)
            r.raise_for_status()
            return r.json().get("issues", [])


# ──────────────────────────────────────────────────────────────────────────────
# XrayScraper — Playwright for the addon iframe
# ──────────────────────────────────────────────────────────────────────────────


class XrayScraper:
    """Scrape Xray Cloud panels via Playwright. Persistent cookies in storage_state.json.

    Uses the **sync** Playwright API run inside a worker thread so it works under
    uvicorn's WindowsSelectorEventLoopPolicy (which forbids subprocess on Windows).
    The class still exposes ``async`` methods so callers don't change.
    """

    def __init__(self, base_url: str | None = None, headless: bool = True):
        s = get_settings()
        self.base_url = (base_url or s.jira_base_url).rstrip("/")
        self.headless = headless

    async def __aenter__(self) -> "XrayScraper":
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    # ── thread-bounded sync helpers ───────────────────────────────────────

    def _new_context(self, pw):
        browser = pw.chromium.launch(headless=self.headless)
        ctx_kwargs: dict[str, Any] = {"viewport": {"width": 1600, "height": 1000}}
        if STORAGE_STATE.exists():
            ctx_kwargs["storage_state"] = str(STORAGE_STATE)
        ctx = browser.new_context(**ctx_kwargs)
        return browser, ctx

    def _open_issue_sync(self, ctx, key: str):
        page = ctx.new_page()
        # Jira boards long-poll forever, so don't wait for networkidle.
        page.goto(f"{self.base_url}/browse/{key}", wait_until="domcontentloaded", timeout=45_000)
        # Wait for either the issue header or a login redirect.
        try:
            page.wait_for_selector(
                '[data-testid*="issue-view"], [data-test-id*="issue.views"], h1, input[name="username"], input[type="email"]',
                timeout=20_000,
            )
        except Exception:
            pass
        if "id.atlassian.com" in page.url or "/login" in page.url:
            log.warning("Auth bounce — final URL was %s; dumping debug snapshot", page.url)
            _dump_debug_sync(page, f"auth-bounce-{key}")
            page.close()
            raise JiraAuthError(
                f"Not authenticated (landed on {page.url}). "
                "Re-run `uv run python -m app.tools.scraper_cli login` and complete SSO."
            )
        return page

    def _login_interactive_sync(self) -> None:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser, ctx = self._new_context(pw)
            try:
                page = ctx.new_page()
                page.goto(f"{self.base_url}/secure/MyJiraHome.jspa", wait_until="domcontentloaded")
                input(">>> press ENTER once you see your Jira dashboard: ")
                ctx.storage_state(path=str(STORAGE_STATE))
                log.info("Saved storage state to %s", STORAGE_STATE)
                page.close()
            finally:
                ctx.close()
                browser.close()

    def _fetch_execution_tests_sync(self, exec_key: str) -> dict[str, Any]:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser, ctx = self._new_context(pw)
            try:
                page = self._open_issue_sync(ctx, exec_key)
                try:
                    # Give the Xray addon iframe time to mount + fetch its rows.
                    page.wait_for_timeout(6_000)
                    tests = _collect_test_anchors_sync(page, exec_key)
                    if not tests:
                        _dump_debug_sync(page, f"execution-{exec_key}")
                        log.warning("Xray test list empty for %s; dumped debug snapshot", exec_key)
                    result = {
                        "exec_key": exec_key,
                        "tests": tests,
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                    }
                    _write_cache(f"execution-{exec_key}.json", result)
                    return result
                finally:
                    page.close()
            finally:
                ctx.close()
                browser.close()

    def _fetch_xray_test_steps_sync(self, test_key: str) -> dict[str, Any]:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser, ctx = self._new_context(pw)
            try:
                page = self._open_issue_sync(ctx, test_key)
                try:
                    _prime_xray_panel(page)
                    steps = _collect_steps_sync(page)
                    if not steps:
                        _dump_debug_sync(page, f"test-{test_key}")
                        log.warning("No steps found for %s; dumped debug snapshot", test_key)
                    result = {
                        "test_key": test_key,
                        "steps": steps,
                        "step_count": len(steps),
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                    }
                    _write_cache(f"test-{test_key}.json", result)
                    return result
                finally:
                    page.close()
            finally:
                ctx.close()
                browser.close()

    # ── async wrappers (off the event loop) ───────────────────────────────

    async def login_interactive(self) -> None:
        if self.headless:
            raise RuntimeError("login_interactive() requires headless=False")
        await asyncio.to_thread(self._login_interactive_sync)

    async def fetch_execution_tests(self, exec_key: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._fetch_execution_tests_sync, exec_key)

    async def fetch_xray_test_steps(self, test_key: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._fetch_xray_test_steps_sync, test_key)


# ──────────────────────────────────────────────────────────────────────────────
# Cache helpers
# ──────────────────────────────────────────────────────────────────────────────


def _write_cache(filename: str, payload: dict[str, Any]) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    p = CACHE_DIR / filename
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return p


def read_cache(filename: str) -> dict[str, Any] | None:
    p = CACHE_DIR / filename
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


DEBUG_DIR = DATA_DIR / "xray-debug"


def _prime_xray_panel(page) -> None:
    """Scroll the Xray Test panel iframe into view and switch to the Steps tab.

    The Xray Cloud Connect addon ships a single iframe (``…/view/webpanel/test/all-in-one``)
    whose JS bundle only fetches step data once the iframe enters the viewport. Inside,
    the panel has a tab strip — the Steps tab is what we need.
    """
    # 1. Find + scroll the Xray host iframe element into view.
    try:
        host = page.locator('iframe[src*="xray.cloud.getxray.app/view/webpanel/test"]').first
        host.wait_for(state="attached", timeout=15_000)
        host.scroll_into_view_if_needed(timeout=5_000)
    except Exception as e:
        log.debug("xray host iframe scroll failed: %s", e)

    # 2. Give the bundle time to fetch + render initial content.
    page.wait_for_timeout(8_000)

    # 3. Locate the Xray frame and click the Steps tab if present.
    xray_frame = None
    for f in page.frames:
        if "xray.cloud.getxray.app/view/webpanel/test" in (f.url or ""):
            xray_frame = f
            break
    if xray_frame is None:
        log.debug("xray frame not found after wait")
        return

    candidates = [
        'role=tab[name=/^Steps/i]',
        'role=tab[name=/Test Details/i]',
        'button:has-text("Steps")',
        '[data-testid*="steps" i]',
        'a:has-text("Steps")',
    ]
    for sel in candidates:
        try:
            loc = xray_frame.locator(sel).first
            loc.wait_for(timeout=2_500)
            loc.click(timeout=2_500)
            page.wait_for_timeout(2_500)
            break
        except Exception:
            continue

    # 4. Wait for any row to appear in the panel.
    try:
        xray_frame.wait_for_selector(
            "table tr, [role='row'], [class*='step' i]",
            timeout=15_000,
        )
    except Exception:
        pass


def _collect_test_anchors_sync(page, exec_key: str) -> list[dict[str, str]]:
    """Walk every frame; collect anchors that point to /browse/<KEY> rows."""
    seen: set[str] = set()
    tests: list[dict[str, str]] = []
    for f in list(page.frames):
        try:
            f.wait_for_selector('a[href*="/browse/"]', timeout=8_000, state="attached")
        except Exception:
            continue
    for f in list(page.frames):
        try:
            anchors = f.locator('a[href*="/browse/"]').all()
        except Exception:
            continue
        for a in anchors:
            try:
                href = a.get_attribute("href") or ""
            except Exception:
                continue
            key = href.rstrip("/").split("/")[-1].split("?")[0]
            if not key or "-" not in key or key == exec_key or key in seen:
                continue
            if not any(ch.isdigit() for ch in key):
                continue
            seen.add(key)
            try:
                text = (a.text_content() or "").strip()
            except Exception:
                text = ""
            tests.append({"key": key, "title": text})
    return tests


def _collect_steps_sync(page) -> list[dict[str, Any]]:
    """Walk every frame; first try a #/Action/Data/Expected table, then fall back
    to Xray's div-based step row pattern."""
    # Pass 1: classic table rows where cells[0] is the step number.
    for f in list(page.frames):
        out: list[dict[str, Any]] = []
        try:
            rows = f.locator("table tr, [role='row']").all()
        except Exception:
            continue
        for r in rows:
            try:
                cells = r.locator("td, [role='cell'], [role='gridcell']").all_text_contents()
            except Exception:
                continue
            cells = [c.strip() for c in cells]
            if len(cells) < 3 or not cells[0].isdigit():
                continue
            out.append(
                {
                    "idx": int(cells[0]),
                    "action": cells[1] if len(cells) > 1 else "",
                    "data": cells[2] if len(cells) > 2 else "",
                    "expected": cells[3] if len(cells) > 3 else "",
                }
            )
        if out:
            return out

    # Pass 2: Xray div-row pattern — any element with class containing 'step'
    # and at least two child blocks that look like Action / Expected.
    for f in list(page.frames):
        if "xray.cloud.getxray.app" not in (f.url or ""):
            continue
        try:
            rows = f.locator("[class*='step-row' i], [class*='stepRow' i], [data-testid*='step' i]").all()
        except Exception:
            continue
        out2: list[dict[str, Any]] = []
        for i, r in enumerate(rows, start=1):
            try:
                blocks = r.locator("[class*='action' i], [class*='data' i], [class*='expect' i], [class*='result' i]").all_text_contents()
                text_all = (r.text_content() or "").strip()
            except Exception:
                continue
            blocks = [b.strip() for b in blocks if b.strip()]
            if not blocks and not text_all:
                continue
            out2.append(
                {
                    "idx": i,
                    "action": blocks[0] if len(blocks) >= 1 else text_all,
                    "data": blocks[1] if len(blocks) >= 2 else "",
                    "expected": blocks[2] if len(blocks) >= 3 else "",
                }
            )
        if out2:
            return out2

    # Pass 3: Xray Generic / Unstructured test — the panel renders the test
    # description as an ADF block (no step rows). Pattern is:
    #   <ol><li><p>action 1</p></li>…</ol>
    #   <p><strong>Expected Results:</strong></p>
    #   <p>expected text…</p>
    # Each <li> becomes one step; the trailing expected paragraphs are attached
    # to the final step.
    for f in list(page.frames):
        if "xray.cloud.getxray.app" not in (f.url or ""):
            continue
        try:
            parsed = f.evaluate(
                """() => {
                    const out = { items: [], expected: '' };
                    // pick the largest <ol> on the page — that's the action list.
                    const ols = Array.from(document.querySelectorAll('ol'));
                    if (!ols.length) return out;
                    ols.sort((a, b) => b.querySelectorAll('li').length - a.querySelectorAll('li').length);
                    const ol = ols[0];
                    out.items = Array.from(ol.querySelectorAll(':scope > li'))
                        .map(li => (li.innerText || '').trim())
                        .filter(Boolean);
                    // Find "Expected Results" marker and grab following paragraphs
                    // until the next heading-like element.
                    const all = Array.from(document.querySelectorAll('p, h1, h2, h3, h4, div'));
                    const idx = all.findIndex(el => /expected\\s*results?/i.test(el.innerText || ''));
                    if (idx >= 0) {
                        const parts = [];
                        for (let i = idx + 1; i < Math.min(all.length, idx + 6); i++) {
                            const t = (all[i].innerText || '').trim();
                            if (!t) continue;
                            if (/^(action|preconditions?|steps?)\\s*:?$/i.test(t)) break;
                            parts.push(t);
                            if (parts.join(' ').length > 600) break;
                        }
                        out.expected = parts.join('\\n').trim();
                    }
                    return out;
                }"""
            )
        except Exception:
            continue
        items = [s for s in (parsed or {}).get("items", []) if s]
        expected = (parsed or {}).get("expected", "") or ""
        if not items:
            continue
        out3: list[dict[str, Any]] = []
        for i, action in enumerate(items, start=1):
            out3.append(
                {
                    "idx": i,
                    "action": action,
                    "data": "",
                    "expected": expected if i == len(items) else "",
                }
            )
        if out3:
            return out3

    return []


def _dump_debug_sync(page, tag: str) -> None:
    """Write screenshot + main HTML + per-frame HTML for triage."""
    try:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(DEBUG_DIR / f"{tag}.png"), full_page=True)
        (DEBUG_DIR / f"{tag}.url.txt").write_text(page.url, encoding="utf-8")
        (DEBUG_DIR / f"{tag}.html").write_text(page.content(), encoding="utf-8")
        manifest: list[str] = []
        for i, f in enumerate(page.frames):
            try:
                html = f.content()
            except Exception as e:
                html = f"<!-- frame.content() failed: {e} -->"
            (DEBUG_DIR / f"{tag}.frame{i}.html").write_text(html, encoding="utf-8")
            manifest.append(f"frame{i}\tname={f.name!r}\turl={f.url}")
        (DEBUG_DIR / f"{tag}.frames.tsv").write_text("\n".join(manifest), encoding="utf-8")
        log.info("wrote Xray debug dump to %s with tag %s", DEBUG_DIR, tag)
    except Exception as e:
        log.warning("debug dump failed: %s", e)
