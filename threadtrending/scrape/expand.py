"""Click the "Xem N câu trả lời" buttons so nested replies load.

Those replies are not in the server-rendered HTML -- they arrive by XHR after a
click, which is why :mod:`.post` also intercepts ``/graphql/query``. Both the
click count and the wall-clock time are capped: a busy thread can have dozens of
these and expanding all of them is not worth the minutes.
"""
from __future__ import annotations

import logging
import time
from typing import Any

log = logging.getLogger(__name__)

#: Threads localises the CTA, so match on both languages plus the bare count.
CTA_PATTERNS = (
    "Xem",
    "câu trả lời",
    "View",
    "replies",
    "repl",
)

CLICKABLE_SELECTOR = (
    "div[role='button'], span[role='button'], button, a[role='link']"
)


def _looks_like_cta(text: str) -> bool:
    lowered = (text or "").strip().lower()
    if not lowered or len(lowered) > 48:
        return False
    has_verb = any(p.lower() in lowered for p in ("xem", "view", "show", "thêm", "more"))
    has_noun = any(p.lower() in lowered for p in ("trả lời", "repl", "phản hồi"))
    return has_verb and has_noun


def expand_replies(page: Any, *, max_expansions: int = 25, budget_s: float = 20.0) -> int:
    """Click reply-expanding buttons until the caps are hit. Returns the count."""
    deadline = time.time() + budget_s
    expanded = 0
    clicked_texts: set[str] = set()

    while expanded < max_expansions and time.time() < deadline:
        target = None
        try:
            for handle in page.query_selector_all(CLICKABLE_SELECTOR):
                try:
                    text = (handle.inner_text() or "").strip()
                except Exception:
                    continue
                if not _looks_like_cta(text):
                    continue
                key = f"{text}@{handle.bounding_box() or ''}"
                if key in clicked_texts:
                    continue
                clicked_texts.add(key)
                target = handle
                break
        except Exception as exc:
            log.debug("scanning for reply CTAs failed: %s", exc)
            break

        if target is None:
            break

        try:
            target.scroll_into_view_if_needed(timeout=2000)
            with page.expect_response(
                lambda r: "/graphql/query" in r.url, timeout=6000
            ):
                target.click(timeout=3000)
            expanded += 1
            page.wait_for_timeout(350)
        except Exception as exc:
            # A CTA that no longer exists, or a click that loaded nothing new.
            log.debug("reply expansion click failed: %s", exc)
            continue

    if expanded:
        log.info("expanded %d hidden reply group(s)", expanded)
    return expanded
