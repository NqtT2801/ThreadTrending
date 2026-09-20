"""Rebuild the real Threads conversation forest from ``thread_items`` groups.

The semantics that every naive scraper gets wrong:

    A ``thread_items`` array of length N is ONE direct reply followed by N-1
    inline-expanded descendants, and item *i*'s parent is item *i-1* -- not the
    root. Flattening the array collapses everybody to depth 1 and destroys the
    very structure that tells us who is answering whom.

``parent_reply_id`` is trusted when Threads supplies it and chain position is the
fallback, because the field is absent on some inline expansions.
"""
from __future__ import annotations

import logging
from collections import deque
from typing import Any, Iterable, Sequence

from ..models import Comment, Thread

log = logging.getLogger(__name__)


def _post_of(item: dict[str, Any]) -> dict[str, Any]:
    post = item.get("post")
    return post if isinstance(post, dict) else item


def _text_of(post: dict[str, Any]) -> str:
    caption = post.get("caption")
    if isinstance(caption, dict):
        return (caption.get("text") or "").strip()
    if isinstance(caption, str):
        return caption.strip()
    return (post.get("text") or "").strip()


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def parse_post(item: dict[str, Any]) -> Comment | None:
    """One ``thread_items`` entry -> a :class:`Comment`, or None if unusable."""
    post = _post_of(item)
    pk = str(post.get("pk") or post.get("id") or "").strip()
    if not pk:
        return None

    user = post.get("user") if isinstance(post.get("user"), dict) else {}
    parent_raw = post.get("parent_reply_id") or post.get("parent_post_id")

    return Comment(
        pk=pk,
        text=_text_of(post),
        username=str(user.get("username") or "").strip(),
        code=post.get("code") or None,
        profile_pic_url=str(user.get("profile_pic_url") or ""),
        is_verified=bool(user.get("is_verified")),
        like_count=_int(post.get("like_count")),
        reply_count=_int(post.get("reply_count") or post.get("direct_reply_count")),
        taken_at=_int(post.get("taken_at") or post.get("taken_at_ts")),
        parent_pk=str(parent_raw).strip() if parent_raw else None,
        hidden_replies_cta=item.get("view_replies_cta_string")
        or post.get("view_replies_cta_string"),
    )


def build_forest(
    groups: Iterable[Sequence[dict[str, Any]]],
    root_pk: str | None = None,
) -> tuple[Comment | None, list[Comment]]:
    """Return ``(root, replies)`` with ``parent_pk`` / ``depth`` / ``chain_id`` set."""
    by_pk: dict[str, Comment] = {}
    parent_of: dict[str, str | None] = {}

    for group in groups:
        prev_pk: str | None = root_pk
        for position, item in enumerate(group):
            comment = parse_post(item)
            if comment is None:
                continue

            # Position wins only when Threads did not tell us the parent.
            parent = comment.parent_pk or (None if position == 0 else prev_pk)
            if position == 0 and parent is None:
                parent = root_pk if comment.pk != root_pk else None

            existing = by_pk.get(comment.pk)
            if existing is None or (not existing.text and comment.text):
                by_pk[comment.pk] = comment
            # First writer wins: the HTML payload is the authoritative ordering
            # and later GraphQL merges must not re-parent an existing node.
            parent_of.setdefault(comment.pk, parent)
            prev_pk = comment.pk

    if not by_pk:
        return None, []

    resolved_root_pk = root_pk if root_pk in by_pk else _infer_root(by_pk, parent_of)
    depths, chains = _resolve_depths(by_pk, parent_of, resolved_root_pk)

    root: Comment | None = None
    replies: list[Comment] = []
    order_counter: dict[str, int] = {}

    for pk, comment in by_pk.items():
        depth = depths.get(pk)
        if depth is None:
            # Unreachable from the root: keep it, but as a plain depth-1 reply.
            depth, chain = 1, pk
        else:
            chain = chains.get(pk, pk)

        order = order_counter.get(chain, 0)
        order_counter[chain] = order + 1

        node = Comment(
            **{
                **{f: getattr(comment, f) for f in comment.__slots__},
                "parent_pk": None if depth == 0 else parent_of.get(pk) or resolved_root_pk,
                "depth": depth,
                "chain_id": "" if depth == 0 else chain,
                "order_in_chain": 0 if depth == 0 else order,
            }
        )
        if depth == 0:
            root = node
        else:
            replies.append(node)

    replies.sort(key=lambda c: (c.chain_id, c.depth, c.taken_at, c.pk))
    return root, replies


def _infer_root(by_pk: dict[str, Comment], parent_of: dict[str, str | None]) -> str:
    """Root = the node with no parent inside the set; else the earliest post."""
    orphans = [pk for pk, parent in parent_of.items() if parent is None or parent not in by_pk]
    if orphans:
        return min(orphans, key=lambda pk: (by_pk[pk].taken_at or 1 << 62, pk))
    return min(by_pk, key=lambda pk: (by_pk[pk].taken_at or 1 << 62, pk))


def _resolve_depths(
    by_pk: dict[str, Comment],
    parent_of: dict[str, str | None],
    root_pk: str,
) -> tuple[dict[str, int], dict[str, str]]:
    """BFS outward from the root. ``chain_id`` is the depth-1 ancestor's pk."""
    children: dict[str, list[str]] = {}
    for pk, parent in parent_of.items():
        if pk == root_pk or parent is None or parent not in by_pk:
            continue
        children.setdefault(parent, []).append(pk)

    depths: dict[str, int] = {root_pk: 0}
    chains: dict[str, str] = {}
    queue: deque[str] = deque([root_pk])

    while queue:
        current = queue.popleft()
        for child in children.get(current, ()):
            if child in depths:
                continue  # a cycle, or a node reachable twice
            depths[child] = depths[current] + 1
            chains[child] = child if depths[child] == 1 else chains.get(current, child)
            queue.append(child)

    return depths, chains


def build_thread(
    groups: Iterable[Sequence[dict[str, Any]]],
    *,
    root_pk: str | None = None,
    trend_name: str = "",
    source_url: str = "",
) -> Thread | None:
    root, replies = build_forest(groups, root_pk)
    if root is None:
        return None
    return Thread(
        root=root,
        replies=tuple(replies),
        trend_name=trend_name,
        source_url=source_url,
    )
