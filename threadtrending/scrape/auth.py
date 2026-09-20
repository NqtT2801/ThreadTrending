"""Login state for the persistent Threads session.

Trend / search pages require a signed-in session; individual post permalinks and
their replies do not. So this is only consulted before a trend scrape, and the
app always offers pasting a permalink instead.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

THREADS_HOME = "https://www.threads.com/"
LOGIN_MARKERS = ("/login", "accounts/login")
#: Present in the DOM only once a session exists.
SIGNED_IN_SELECTORS = (
    "a[href*='/activity']",
    "svg[aria-label='Trang chủ']",
    "svg[aria-label='Home']",
    "div[role='navigation'] a[href^='/@']",
)
SESSION_COOKIES = ("sessionid", "ig_did", "ds_user_id")


@dataclass(frozen=True)
class LoginState:
    signed_in: bool
    username: str = ""
    detail: str = ""

    @property
    def badge(self) -> str:
        if self.signed_in:
            return f"Đã đăng nhập{(' · @' + self.username) if self.username else ''}"
        return "Chưa đăng nhập"


def _probe_on_worker(context: Any, timeout_ms: int) -> LoginState:
    cookies = {c["name"] for c in context.cookies()}
    has_cookie = any(name in cookies for name in SESSION_COOKIES)

    page = context.new_page()
    try:
        page.goto(THREADS_HOME, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(1200)
        url = page.url

        if any(marker in url for marker in LOGIN_MARKERS):
            return LoginState(False, detail="Threads chuyển hướng sang trang đăng nhập.")

        for selector in SIGNED_IN_SELECTORS:
            if page.query_selector(selector):
                username = ""
                if handle := page.query_selector("div[role='navigation'] a[href^='/@']"):
                    username = (handle.get_attribute("href") or "").lstrip("/@")
                return LoginState(True, username=username, detail="Phiên đăng nhập hợp lệ.")

        if has_cookie:
            return LoginState(
                True, detail="Có cookie phiên nhưng không thấy thanh điều hướng."
            )
        return LoginState(False, detail="Không tìm thấy dấu hiệu đã đăng nhập.")
    finally:
        page.close()


def probe(browser_manager: Any, *, timeout_ms: int = 25_000) -> LoginState:
    """Check whether the persistent profile still holds a Threads session."""
    try:
        context = browser_manager.scrape_context(headless=True)
        return browser_manager.call(_probe_on_worker, context, timeout_ms)
    except Exception as exc:
        log.warning("login probe failed: %s", exc)
        return LoginState(False, detail=f"Không kiểm tra được: {exc}")


def _wait_for_login_on_worker(context: Any, poll_seconds: int, interval: float) -> LoginState:
    page = context.new_page()
    try:
        page.goto(THREADS_HOME, wait_until="domcontentloaded", timeout=60_000)
        deadline = time.time() + poll_seconds
        while time.time() < deadline:
            page.wait_for_timeout(int(interval * 1000))
            for selector in SIGNED_IN_SELECTORS:
                if page.query_selector(selector):
                    return LoginState(True, detail="Đăng nhập thành công.")
            cookies = {c["name"] for c in context.cookies()}
            if any(name in cookies for name in SESSION_COOKIES):
                return LoginState(True, detail="Đã nhận cookie phiên.")
        return LoginState(False, detail="Hết thời gian chờ đăng nhập.")
    finally:
        page.close()


def open_login_window(
    browser_manager: Any,
    *,
    poll_seconds: int = 240,
    interval: float = 2.0,
) -> LoginState:
    """Open a visible browser so the user can sign in once, then persist it.

    The context is reopened headless afterwards so the rest of the pipeline runs
    without a window popping up.
    """
    browser_manager.close_scrape_context()
    context = browser_manager.scrape_context(headless=False)
    try:
        state = browser_manager.call(
            _wait_for_login_on_worker, context, poll_seconds, interval
        )
    finally:
        browser_manager.close_scrape_context()
    return state
