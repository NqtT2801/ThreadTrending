"""Turn a trending-topic URL into a list of post permalinks.

This is the one step that genuinely needs a signed-in session: Threads serves
logged-out visitors an empty shell for ``/search``. Everything downstream (the
post page and its replies) works without one, which is why a failure here is
recoverable -- the user can paste a permalink instead.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from ..errors import LoginRequired, ScrapeEmpty
from . import sjs
from .url import TrendTarget, canonical_permalink

log = logging.getLogger(__name__)

NAV_TIMEOUT_MS = 45_000
SETTLE_MS = 2_500
SCROLL_STEPS = 5
LOGIN_WALL_MARKERS = ("/login", "accounts/login")
POST_HREF_RE = re.compile(r"/@([A-Za-z0-9._]+)/post/([A-Za-z0-9_-]{5,})")


def _collect_on_worker(context: Any, url: str) -> tuple[str, list[Any], str]:
    captured: list[Any] = []
    page = context.new_page()

    def on_response(response: Any) -> None:
        if "/graphql/query" not in response.url:
            return
        try:
            body = response.text()
        except Exception:
            return
        if "thread_items" not in body:
            return
        try:
            captured.append(json.loads(body))
        except json.JSONDecodeError:
            pass

    page.on("response", on_response)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        page.wait_for_timeout(SETTLE_MS)
        for _ in range(SCROLL_STEPS):
            page.mouse.wheel(0, 2000)
            page.wait_for_timeout(800)
        return page.content(), captured, page.url
    finally:
        try:
            page.remove_listener("response", on_response)
        except Exception:
            pass
        page.close()


def fetch_trend_permalinks(
    browser_manager: Any,
    target: TrendTarget,
    *,
    limit: int = 6,
    headless: bool = True,
) -> list[str]:
    """Ranked post permalinks for a trend page, in the order Threads served them."""
    context = browser_manager.scrape_context(headless=headless)
    started = time.time()
    html, payloads, final_url = browser_manager.call(
        _collect_on_worker, context, target.url
    )

    if any(marker in final_url for marker in LOGIN_WALL_MARKERS):
        raise LoginRequired("Trang trend của Threads yêu cầu đăng nhập.")

    ordered: list[str] = []
    seen: set[str] = set()
    for link in _from_payloads(payloads) + _from_html(html):
        if link not in seen:
            seen.add(link)
            ordered.append(link)

    log.info(
        "trend '%s': %d permalink(s) in %.1fs",
        target.query, len(ordered), time.time() - started,
    )
    if not ordered:
        raise ScrapeEmpty(
            f"Không tìm thấy bài viết nào cho trend '{target.query}'. "
            "Có thể phiên đăng nhập đã hết hạn."
        )
    return ordered[:limit]


def _from_html(html: str) -> list[str]:
    return [
        canonical_permalink(m.group(1), m.group(2))
        for m in POST_HREF_RE.finditer(html)
    ]


def _from_payloads(payloads: list[Any]) -> list[str]:
    """Payload order reflects Threads' own ranking, so these go first."""
    out: list[str] = []
    for payload in payloads:
        for group in sjs.collect_thread_item_groups(payload):
            for item in group:
                post = item.get("post") if isinstance(item.get("post"), dict) else item
                user = post.get("user") or {}
                code, username = post.get("code"), user.get("username")
                if code and username:
                    out.append(canonical_permalink(str(username), str(code)))
    return out
