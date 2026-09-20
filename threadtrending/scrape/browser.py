"""Two Chromium instances, both driven from one dedicated worker thread.

*Scrape browser* -- a persistent context holding the user's Threads session, so
signing in is a once-per-machine action. It also serves ``context.request`` for
avatar downloads: an ``APIRequestContext`` shares the session cookies and is not
subject to CORS, unlike an in-page ``fetch()`` which the CDN rejects outright.

*Render browser* -- headless, profile-less, deterministic. Cards must look the
same regardless of what the scrape session has been doing.

**Threading rule:** Playwright's sync API refuses to run inside a thread that
already owns an asyncio event loop, and Streamlit's ScriptRunner thread changes
between reruns. Everything here therefore executes on one long-lived worker via
:meth:`BrowserManager.call`; no Playwright object is ever touched from outside.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, TypeVar

from ..paths import PATHS

log = logging.getLogger(__name__)

T = TypeVar("T")

RENDER_VIEWPORT = {"width": 1000, "height": 700}
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
    "--no-first-run",
    "--no-default-browser-check",
]


class BrowserManager:
    """Owns the Playwright lifecycle. Call everything through :meth:`call`."""

    def __init__(self, user_data_dir: Path | None = None) -> None:
        self._user_data_dir = user_data_dir or PATHS.profile
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pw")
        self._lock = threading.Lock()
        self._playwright: Any = None
        self._scrape_ctx: Any = None
        self._render_browser: Any = None
        self._render_ctx: Any = None
        self._closed = False

    # --- plumbing ---------------------------------------------------------
    def call(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Run ``fn`` on the Playwright worker thread and return its result."""
        if self._closed:
            raise RuntimeError("BrowserManager đã đóng")
        future: Future[T] = self._executor.submit(fn, *args, **kwargs)
        return future.result()

    def _playwright_instance(self) -> Any:
        if self._playwright is None:
            from playwright.sync_api import sync_playwright

            self._playwright = sync_playwright().start()
        return self._playwright

    # --- scrape browser ---------------------------------------------------
    def _open_scrape_context(self, headless: bool) -> Any:
        pw = self._playwright_instance()
        self._user_data_dir.mkdir(parents=True, exist_ok=True)
        log.info("launching scrape context (headless=%s)", headless)
        return pw.chromium.launch_persistent_context(
            user_data_dir=str(self._user_data_dir),
            headless=headless,
            viewport={"width": 1280, "height": 900},
            user_agent=USER_AGENT,
            locale="vi-VN",
            timezone_id="Asia/Ho_Chi_Minh",
            args=LAUNCH_ARGS,
        )

    def scrape_context(self, *, headless: bool = True) -> Any:
        """The persistent, logged-in context. Reopened if the mode changes."""
        with self._lock:
            if self._scrape_ctx is not None:
                current = getattr(self._scrape_ctx, "_tt_headless", None)
                if current == headless:
                    return self._scrape_ctx
                self._close_scrape_locked()
            ctx = self.call(self._open_scrape_context, headless)
            ctx._tt_headless = headless  # type: ignore[attr-defined]
            self._scrape_ctx = ctx
            return ctx

    def _close_scrape_locked(self) -> None:
        if self._scrape_ctx is not None:
            try:
                self.call(self._scrape_ctx.close)
            except Exception as exc:  # pragma: no cover - best effort
                log.debug("closing scrape context failed: %s", exc)
            self._scrape_ctx = None

    def close_scrape_context(self) -> None:
        with self._lock:
            self._close_scrape_locked()

    # --- render browser ---------------------------------------------------
    def _open_render_context(self, device_scale: int) -> tuple[Any, Any]:
        pw = self._playwright_instance()
        browser = pw.chromium.launch(headless=True, args=LAUNCH_ARGS)
        context = browser.new_context(
            viewport=RENDER_VIEWPORT,
            device_scale_factor=device_scale,
            locale="vi-VN",
            # An opaque page would defeat screenshot(omit_background=True).
            color_scheme="dark",
        )
        return browser, context

    def render_context(self, device_scale: int = 2) -> Any:
        """Clean headless context used only for card screenshots."""
        with self._lock:
            if self._render_ctx is None:
                self._render_browser, self._render_ctx = self.call(
                    self._open_render_context, device_scale
                )
            return self._render_ctx

    # --- lifecycle --------------------------------------------------------
    def shutdown(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for closer in (
                lambda: self._render_ctx and self._render_ctx.close(),
                lambda: self._render_browser and self._render_browser.close(),
                lambda: self._scrape_ctx and self._scrape_ctx.close(),
                lambda: self._playwright and self._playwright.stop(),
            ):
                try:
                    self._executor.submit(closer).result(timeout=20)
                except Exception as exc:  # pragma: no cover - best effort
                    log.debug("shutdown step failed: %s", exc)
            self._render_ctx = self._render_browser = None
            self._scrape_ctx = self._playwright = None
            self._executor.shutdown(wait=False)

    def __enter__(self) -> "BrowserManager":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.shutdown()
