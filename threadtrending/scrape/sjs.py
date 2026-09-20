"""Pull Threads' embedded JSON out of a page.

Two rules learned the hard way:

* Select blocks by *marker substring* on the raw text, not by position. Threads
  ships dozens of ``data-sjs`` blocks and their order is not stable.
* Then walk the parsed object **recursively** looking for any dict that owns a
  ``thread_items`` key. Hardcoding ``result.data.data.edges`` is the single most
  common reason a Threads scraper dies on a Monday morning.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Iterable, Iterator

log = logging.getLogger(__name__)

SJS_BLOCK_RE = re.compile(
    r"<script[^>]*?type=[\"']application/json[\"'][^>]*?data-sjs[^>]*?>(.*?)</script>",
    re.DOTALL | re.IGNORECASE,
)
# Some builds emit the attributes the other way round.
SJS_BLOCK_ALT_RE = re.compile(
    r"<script[^>]*?data-sjs[^>]*?type=[\"']application/json[\"'][^>]*?>(.*?)</script>",
    re.DOTALL | re.IGNORECASE,
)

POST_PAGE_MARKERS = ("thread_items", "BarcelonaPostPage")
ANY_THREAD_MARKERS = ("thread_items",)
SERP_MARKERS = ("searchResults", "thread_items")

MAX_DEPTH = 40


def iter_raw_blocks(html: str) -> Iterator[str]:
    """Yield the raw text of every ``application/json`` + ``data-sjs`` script."""
    seen: set[int] = set()
    for pattern in (SJS_BLOCK_RE, SJS_BLOCK_ALT_RE):
        for match in pattern.finditer(html):
            start = match.start(1)
            if start in seen:
                continue
            seen.add(start)
            yield match.group(1)


def iter_payloads(html: str, markers: Iterable[str] = ANY_THREAD_MARKERS) -> Iterator[Any]:
    """Parse only the blocks whose raw text contains *every* marker."""
    needles = tuple(markers)
    for raw in iter_raw_blocks(html):
        if not all(n in raw for n in needles):
            continue
        try:
            yield json.loads(raw)
        except json.JSONDecodeError as exc:
            log.debug("skipping unparseable data-sjs block: %s", exc)


def walk_for_key(obj: Any, key: str, _depth: int = 0) -> Iterator[dict[str, Any]]:
    """Yield every dict anywhere in ``obj`` that owns ``key``."""
    if _depth > MAX_DEPTH:
        return
    if isinstance(obj, dict):
        if key in obj:
            yield obj
        for value in obj.values():
            yield from walk_for_key(value, key, _depth + 1)
    elif isinstance(obj, list):
        for value in obj:
            yield from walk_for_key(value, key, _depth + 1)


def collect_thread_item_groups(obj: Any) -> list[list[dict[str, Any]]]:
    """Every ``thread_items`` array found anywhere in the payload.

    Each returned group is one *chain*: a direct reply followed by its
    inline-expanded descendants. That shape is what :mod:`.tree` depends on, so
    groups are never flattened here.
    """
    groups: list[list[dict[str, Any]]] = []
    for holder in walk_for_key(obj, "thread_items"):
        items = holder.get("thread_items")
        if isinstance(items, list) and items and all(isinstance(i, dict) for i in items):
            groups.append(items)
    return groups


def dedupe_groups(groups: Iterable[list[dict[str, Any]]]) -> list[list[dict[str, Any]]]:
    """Drop groups whose post-pk sequence we have already seen.

    The same chain arrives from both the embedded HTML and the intercepted
    GraphQL responses; keeping the longer copy preserves the deepest expansion.
    """
    best: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for group in groups:
        key = tuple(_pk_of(item) for item in group)
        if not any(key):
            continue
        prefix_hit = next((k for k in best if _is_prefix(key, k) or _is_prefix(k, key)), None)
        if prefix_hit is None:
            best[key] = group
        elif len(key) > len(prefix_hit):
            best.pop(prefix_hit)
            best[key] = group
    return list(best.values())


def _is_prefix(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    return len(a) <= len(b) and b[: len(a)] == a


def _pk_of(item: dict[str, Any]) -> str:
    post = item.get("post") if isinstance(item.get("post"), dict) else item
    return str(post.get("pk") or post.get("id") or "")


def find_root_pk(obj: Any) -> str | None:
    """Best-effort root post pk: the first item of the first group that has one."""
    return find_root_pk_from_groups(collect_thread_item_groups(obj))


def find_root_pk_from_groups(groups: Iterable[list[dict[str, Any]]]) -> str | None:
    """The root is the first item of the group nobody else replies to.

    On a post page Threads emits the root post as its own single-item group
    before the reply groups, so preferring a group whose first item declares no
    parent picks it out reliably.
    """
    groups = list(groups)
    for group in groups:
        if group and not _parent_of(group[0]) and (pk := _pk_of(group[0])):
            return pk
    for group in groups:
        if group and (pk := _pk_of(group[0])):
            return pk
    return None


def _parent_of(item: dict[str, Any]) -> str:
    post = item.get("post") if isinstance(item.get("post"), dict) else item
    return str(post.get("parent_reply_id") or post.get("parent_post_id") or "")


def iter_post_codes(html: str) -> list[str]:
    """Shortcodes linked from a SERP page, in document order, deduped."""
    codes: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"/@[^/\"']+/post/([A-Za-z0-9_-]{5,})", html):
        code = match.group(1)
        if code not in seen:
            seen.add(code)
            codes.append(code)
    return codes
