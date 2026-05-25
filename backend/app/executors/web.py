"""Web Client executor — sync Playwright wrapped in asyncio.to_thread.

Why sync: under uvicorn on Windows, the running event loop is a
``WindowsSelectorEventLoopPolicy`` loop. Playwright's async API requires
``subprocess_exec`` which raises ``NotImplementedError`` there. The fix that
worked for `XrayScraper` is to use the sync API in a worker thread; we mirror
that pattern here so launches succeed inside the WS handler.
"""
from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, TypeVar

from app.executors import ActionResult, Executor
from app.tools.clients_config import WebCfg, load_clients_config

log = logging.getLogger(__name__)

T = TypeVar("T")


class WebExecutor(Executor):
    name = "web"

    def __init__(self, cfg: WebCfg | None = None):
        self.cfg = cfg or load_clients_config().web
        self._pw: Any = None
        self._browser: Any = None
        self._ctx: Any = None
        self._page: Any = None
        self._lock = asyncio.Lock()
        self.logged_in: bool = False
        # Sync Playwright objects are pinned to the thread that created them
        # (greenlet affinity). Keep one dedicated worker for every call.
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="web-pw")

    async def _run(self, fn: Callable[..., T], *args: Any) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._pool, lambda: fn(*args))

    # ── sync workers ─────────────────────────────────────────────────────

    def _start_sync(self) -> str:
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        browser_name = self.cfg.browser if self.cfg.browser != "chrome" else "chromium"
        launcher = getattr(self._pw, browser_name)
        launch_kwargs: dict[str, Any] = {"headless": self.cfg.headless}
        if self.cfg.browser == "chrome":
            launch_kwargs["channel"] = "chrome"
        elif self.cfg.browser == "msedge":
            launcher = self._pw.chromium
            launch_kwargs["channel"] = "msedge"
        self._browser = launcher.launch(**launch_kwargs)
        ctx_kwargs: dict[str, Any] = {
            "viewport": {"width": 1600, "height": 1000},
            "ignore_https_errors": True,
        }
        # Reuse Jira/OnBase cookies from any prior interactive session.
        from pathlib import Path as _P
        data_dir = _P(__file__).resolve().parents[3] / "data"
        web_state = data_dir / "web_storage_state.json"
        jira_state = data_dir / "storage_state.json"
        chosen = web_state if web_state.exists() else (jira_state if jira_state.exists() else None)
        if chosen is not None:
            ctx_kwargs["storage_state"] = str(chosen)
            log.info("WebExecutor loading storage_state from %s", chosen)
        self._ctx = self._browser.new_context(**ctx_kwargs)
        self._page = self._ctx.new_page()
        # Follow popup windows (e.g. "Open Workflow" opens a new tab).
        self._ctx.on("page", self._on_new_page)
        self._page.goto(self.cfg.url, wait_until="domcontentloaded", timeout=60_000)
        self._maybe_auto_login_sync()
        # If we landed somewhere that isn't a login page, treat the session as
        # already authenticated (storage_state did the job).
        cur = (self._page.url or "").lower()
        if "login.aspx" not in cur and "/login" not in cur:
            self.logged_in = True
        return self.cfg.url

    def _on_new_page(self, new_page) -> None:
        try:
            new_page.wait_for_load_state("domcontentloaded", timeout=30_000)
        except Exception:
            pass
        log.info("WebExecutor switched to popup page: %s", new_page.url)
        self._page = new_page

    def _maybe_auto_login_sync(self) -> None:
        """If the OnBase Login.aspx form is present, fill it from env creds."""
        from app.config import get_settings
        from pathlib import Path as _P

        s = get_settings()
        user, pwd = s.onbase_web_user, s.onbase_web_pass
        page = self._page
        # Detect login page by URL or title
        url = (page.url or "").lower()
        on_login = "login.aspx" in url or "/login" in url
        if not on_login:
            # also detect by presence of a password field
            try:
                on_login = page.locator("input[type='password']").count() > 0
            except Exception:
                pass
        if not on_login:
            log.info("auto-login: not on login page (url=%s) — skipping", page.url)
            return
        if not (user and pwd):
            log.warning(
                "auto-login: on login page (%s) but ONBASE_WEB_USER/ONBASE_WEB_PASS "
                "are unset. Edit .env.",
                page.url,
            )
            self._dump_login_page_sync("login-noauth")
            return
        user_selectors = [
            "#txtUserId", "#UserName", "#txtUserName", "#username",
            "input[name='UserName']", "input[name='txtUserId']",
            "input[name='txtUserName']", "input[name='username']",
            "input[id*='User' i]", "input[name*='user' i]",
            "input[type='text']:visible",
        ]
        pass_selectors = [
            "#txtPassword", "#Password", "#password",
            "input[name='Password']", "input[name='txtPassword']",
            "input[name='password']", "input[type='password']",
        ]
        submit_selectors = [
            "#btnLogin", "#btnSubmit", "#loginButton",
            "button[type='submit']", "input[type='submit']",
            "button:has-text('Login')", "button:has-text('Log In')",
            "input[value*='Login' i]", "input[value*='Log In' i]",
        ]
        try:
            u = self._first_present(page, user_selectors, timeout=5_000)
            p = self._first_present(page, pass_selectors, timeout=3_000)
            if not (u and p):
                log.warning("auto-login: could not find username/password fields")
                self._dump_login_page_sync("login-no-fields")
                return
            u.fill(user, timeout=4_000)
            p.fill(pwd, timeout=4_000)
            btn = self._first_present(page, submit_selectors, timeout=2_000)
            if btn:
                btn.click(timeout=4_000)
            else:
                p.press("Enter")
            page.wait_for_load_state("domcontentloaded", timeout=30_000)
            # Confirm we left Login.aspx
            new_url = (page.url or "").lower()
            if "login.aspx" in new_url or "/login" in new_url:
                log.warning("auto-login: still on login page after submit (%s)", page.url)
                self._dump_login_page_sync("login-rejected")
            else:
                log.info("auto-login: success, now at %s", page.url)
                self.logged_in = True
                # Persist OnBase cookies separately so we don't clobber the
                # Jira/Xray storage_state used by the scraper.
                try:
                    out = _P(__file__).resolve().parents[3] / "data" / "web_storage_state.json"
                    self._ctx.storage_state(path=str(out))
                    log.info("auto-login: saved web storage_state to %s", out)
                except Exception as e:
                    log.warning("auto-login: storage_state save failed: %s", e)
        except Exception as e:
            log.warning("auto-login skipped: %s", e)
            self._dump_login_page_sync("login-exception")

    def _dump_login_page_sync(self, tag: str) -> None:
        from pathlib import Path as _P
        try:
            d = _P(__file__).resolve().parents[3] / "data" / "web-debug"
            d.mkdir(parents=True, exist_ok=True)
            self._page.screenshot(path=str(d / f"{tag}.png"), full_page=True)
            (d / f"{tag}.html").write_text(self._page.content(), encoding="utf-8")
            (d / f"{tag}.url.txt").write_text(self._page.url, encoding="utf-8")
            log.info("wrote login debug dump %s/%s.*", d, tag)
        except Exception as e:
            log.debug("dump failed: %s", e)

    @staticmethod
    def _first_present(page, selectors: list[str], timeout: int):
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                loc.wait_for(state="visible", timeout=timeout)
                return loc
            except Exception:
                continue
        return None

    def _stop_sync(self) -> None:
        for closer in (
            lambda: self._ctx and self._ctx.close(),
            lambda: self._browser and self._browser.close(),
            lambda: self._pw and self._pw.stop(),
        ):
            try:
                closer()
            except Exception as e:
                log.debug("close step: %s", e)
        self._pw = self._browser = self._ctx = self._page = None

    def _locate_sync(self, target: str):
        page = self._page
        frames = [page.main_frame] + [f for f in page.frames if f is not page.main_frame]
        for frame in frames:
            for role in ("button", "link", "tab", "menuitem", "checkbox", "textbox", "treeitem"):
                try:
                    loc = frame.get_by_role(role, name=target)
                    if loc.count():
                        return loc.first
                except Exception:
                    pass
            try:
                loc = frame.get_by_text(target, exact=False)
                if loc.count():
                    return loc.first
            except Exception:
                pass
            try:
                loc = frame.locator(f"[data-testid='{target}']")
                if loc.count():
                    return loc.first
            except Exception:
                pass
            try:
                loc = frame.locator(target)
                if loc.count():
                    return loc.first
            except Exception:
                pass
        return None

    def _click_sync(self, target: str) -> ActionResult:
        loc = self._locate_sync(target)
        if loc is None:
            return ActionResult(ok=False, detail=f"no locator matched {target!r}")
        try:
            loc.click(timeout=10_000)
            return ActionResult(ok=True, detail=f"clicked {target!r}")
        except Exception as e:
            return ActionResult(ok=False, detail=f"click {target!r}: {type(e).__name__}: {e!r}")

    def _type_sync(self, target: str, text: str) -> ActionResult:
        loc = self._locate_sync(target)
        if loc is None:
            return ActionResult(ok=False, detail=f"no locator matched {target!r}")
        try:
            loc.fill(text, timeout=10_000)
            return ActionResult(ok=True, detail=f"filled {target!r}")
        except Exception as e:
            return ActionResult(ok=False, detail=f"type {target!r}: {type(e).__name__}: {e!r}")

    def _screenshot_sync(self, dest: Path | None) -> ActionResult:
        png = self._page.screenshot(full_page=False)
        if dest:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(png)
            return ActionResult(ok=True, detail=str(dest), screenshot=dest)
        return ActionResult(ok=True, detail=f"{len(png)} bytes")

    def _stream_frame_sync(self) -> bytes:
        cfg = load_clients_config().stream
        return self._page.screenshot(type="jpeg", quality=cfg.jpeg_quality, full_page=False)

    def _hi_screenshot_sync(self) -> bytes:
        """Higher-quality JPEG for the vision LLM (live stream is too lossy)."""
        return self._page.screenshot(type="jpeg", quality=85, full_page=False)

    def _describe_sync(self) -> str:
        title = self._page.title()
        url = self._page.url
        try:
            text = self._page.locator("body").inner_text(timeout=2_000)
        except Exception:
            text = ""
        snippet = " | ".join(line.strip() for line in text.splitlines() if line.strip())[:1500]
        return f"[web] {title} <{url}>\n{snippet}"

    # ── async surface ────────────────────────────────────────────────────

    async def start(self) -> ActionResult:
        async with self._lock:
            try:
                url = await self._run(self._start_sync)
            except Exception as e:
                log.exception("WebExecutor start failed")
                try:
                    await self._run(self._stop_sync)
                except Exception:
                    pass
                return ActionResult(ok=False, detail=f"{type(e).__name__}: {e!r}")
            return ActionResult(ok=True, detail=f"web client open at {url}")

    async def stop(self) -> None:
        async with self._lock:
            try:
                await self._run(self._stop_sync)
            finally:
                self._pool.shutdown(wait=False, cancel_futures=True)

    async def click(self, target: str) -> ActionResult:
        async with self._lock:
            return await self._run(self._click_sync, target)

    async def type_text(self, target: str, text: str) -> ActionResult:
        async with self._lock:
            return await self._run(self._type_sync, target, text)

    async def screenshot(self, dest: Path | None = None) -> ActionResult:
        async with self._lock:
            return await self._run(self._screenshot_sync, dest)

    async def stream_frame(self) -> bytes:
        async with self._lock:
            return await self._run(self._stream_frame_sync)

    async def hi_screenshot(self) -> bytes:
        async with self._lock:
            return await self._run(self._hi_screenshot_sync)

    async def describe(self) -> str:
        async with self._lock:
            return await self._run(self._describe_sync)
