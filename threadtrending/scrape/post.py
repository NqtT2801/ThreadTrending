"""Fetch a Threads post and its reply forest.

Two extraction sources, merged and deduped:

**A. Embedded JSON** -- ``<script type="application/json" data-sjs>`` blocks. This
is everything the server rendered.

**B. Intercepted GraphQL** -- ``page.on("response")`` on ``/graphql/query``. This
is the only way to see replies hidden behind a "Xem N câu trả lời" button, which
are fetched by XHR after a click.

All Playwright work happens inside one function run on the browser worker
thread, so the response handler and the navigation share a thread and no
cross-thread coordination is needed.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..errors import ScrapeEmpty
from ..models import Thread
from . import sjs
from .expand import expand_replies
from .tree import build_thread

log = logging.getLogger(__name__)

NAV_TIMEOUT_MS = 45_000
SETTLE_MS = 1_800
SCROLL_STEPS = 4
GRAPHQL_MARKER = "/graphql/query"


@dataclass
class ScrapeResult:
    thread: Thread | None
    html: str = ""
    payloads: list[Any] = field(default_factory=list)
    expansions: int = 0
    group_count: int = 0

    @property
    def reply_count(self) -> int:
        return len(self.thread.replies) if self.thread else 0


def _collect_on_worker(
    context: Any,
    url: str,
    *,
    expand: bool,
    max_expansions: int,
    expand_budget_s: float,
) -> tuple[str, list[Any], int]:
    """Navigate, capture GraphQL bodies, expand hidden replies, return raw data."""
    captured: list[Any] = []
    page = context.new_page()

    def on_response(response: Any) -> None:
        if GRAPHQL_MARKER not in response.url:
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

        # Threads lazy-loads replies on scroll before any button is involved.
        for _ in range(SCROLL_STEPS):
            page.mouse.wheel(0, 1600)
            page.wait_for_timeout(600)

        expansions = 0
        if expand:
            expansions = expand_replies(
                page, max_expansions=max_expansions, budget_s=expand_budget_s
            )

        page.wait_for_timeout(500)
        html = page.content()
        return html, captured, expansions
    finally:
        try:
            page.remove_listener("response", on_response)
        except Exception:
            pass
        page.close()


def fetch_thread(
    browser_manager: Any,
    url: str,
    *,
    trend_name: str = "",
    headless: bool = True,
    expand: bool = True,
    max_expansions: int = 25,
    expand_budget_s: float = 20.0,
    debug_dir: Path | None = None,
) -> ScrapeResult:
    """Scrape one post permalink into a :class:`Thread`."""
    context = browser_manager.scrape_context(headless=headless)
    started = time.time()

    html, payloads, expansions = browser_manager.call(
        _collect_on_worker, context, url,
        expand=expand, max_expansions=max_expansions,
        expand_budget_s=expand_budget_s,
    )

    groups: list[list[dict[str, Any]]] = []
    for payload in sjs.iter_payloads(html, sjs.ANY_THREAD_MARKERS):
        groups.extend(sjs.collect_thread_item_groups(payload))
    for payload in payloads:
        groups.extend(sjs.collect_thread_item_groups(payload))

    groups = sjs.dedupe_groups(groups)
    root_pk = sjs.find_root_pk_from_groups(groups)
    thread = build_thread(groups, root_pk=root_pk, trend_name=trend_name, source_url=url)

    if debug_dir is not None:
        _dump_debug(debug_dir, url, html, payloads, groups)

    log.info(
        "scraped %s in %.1fs: %d groups, %d replies, %d expansions",
        url, time.time() - started, len(groups),
        len(thread.replies) if thread else 0, expansions,
    )
    return ScrapeResult(
        thread=thread, html=html, payloads=payloads,
        expansions=expansions, group_count=len(groups),
    )


def fetch_thread_or_raise(browser_manager: Any, url: str, **kwargs: Any) -> Thread:
    result = fetch_thread(browser_manager, url, **kwargs)
    if result.thread is None or not result.thread.replies:
        raise ScrapeEmpty(
            f"Không lấy được bình luận nào từ {url} "
            f"({result.group_count} nhóm thread_items)."
        )
    return result.thread


def thread_from_json(raw: str, *, trend_name: str = "", source_url: str = "") -> Thread:
    """Escape hatch: build a Thread from JSON the user pasted by hand.

    Exists so a broken scraper never blocks a render -- the user can open the
    post, copy a ``data-sjs`` payload out of devtools, and carry on.
    """
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ScrapeEmpty(f"JSON không hợp lệ: {exc}") from exc

    groups = sjs.dedupe_groups(sjs.collect_thread_item_groups(payload))
    if not groups:
        raise ScrapeEmpty("Không tìm thấy 'thread_items' trong JSON đã dán.")

    thread = build_thread(
        groups, root_pk=sjs.find_root_pk_from_groups(groups),
        trend_name=trend_name, source_url=source_url,
    )
    if thread is None:
        raise ScrapeEmpty("Không dựng được cây bình luận từ JSON đã dán.")
    return thread


def _dump_debug(debug_dir: Path, url: str, html: str,
                payloads: list[Any], groups: list[list[dict[str, Any]]]) -> None:
    """Persist the raw page so a payload change can be diagnosed after the fact."""
    try:
        debug_dir.mkdir(parents=True, exist_ok=True)
        (debug_dir / "page.html").write_text(html, encoding="utf-8")
        (debug_dir / "graphql.json").write_text(
            json.dumps(payloads, ensure_ascii=False)[:4_000_000], encoding="utf-8"
        )
        (debug_dir / "groups.json").write_text(
            json.dumps(groups, ensure_ascii=False)[:4_000_000], encoding="utf-8"
        )
        (debug_dir / "source.txt").write_text(url, encoding="utf-8")
    except OSError as exc:  # pragma: no cover - diagnostics only
        log.debug("could not write debug dump: %s", exc)
