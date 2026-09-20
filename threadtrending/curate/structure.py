"""The structural layer: a real reply is a real interaction.

Everything else in curation is inference from word choice. This module works
from the reply graph itself, which is ground truth -- if B's ``parent_pk`` is A,
then B literally answered A, no lexicon required.

Two things come out of here:

* :func:`mine_chains` -- windows of genuine back-and-forth, used to seed the
  assembler so that real dialogue beats merely-similar-sounding comments.
* :func:`cohesion` -- how strongly comment *b* reads as a response to *a*. This
  is the term that makes the finished video feel like a conversation.
"""
from __future__ import annotations

import math
import statistics
from typing import Iterable, Sequence

from ..models import Chain, Comment
from . import lexicons as lex
from . import normalize as nz
from .scoring import CorpusStats

MIN_CHAIN_LEN = 2
MAX_WINDOW = 4
#: Below this, two replies read as one live exchange rather than a next-day reply.
FAST_EXCHANGE_S = 600.0


def children_map(comments: Iterable[Comment]) -> dict[str, list[Comment]]:
    out: dict[str, list[Comment]] = {}
    for comment in comments:
        if comment.parent_pk:
            out.setdefault(comment.parent_pk, []).append(comment)
    for bucket in out.values():
        bucket.sort(key=lambda c: (c.taken_at, c.pk))
    return out


def root_to_leaf_paths(
    comments: Sequence[Comment],
    root_pk: str,
    *,
    max_paths: int = 400,
) -> list[list[Comment]]:
    """Every path from a depth-1 reply down to a leaf."""
    kids = children_map(comments)
    by_pk = {c.pk: c for c in comments}
    paths: list[list[Comment]] = []

    def walk(node: Comment, acc: list[Comment]) -> None:
        if len(paths) >= max_paths:
            return
        acc = acc + [node]
        descendants = [c for c in kids.get(node.pk, ()) if c.pk in by_pk]
        if not descendants:
            paths.append(acc)
            return
        for child in descendants:
            walk(child, acc)

    for top in kids.get(root_pk, ()):
        if top.pk in by_pk:
            walk(top, [])
    return paths


def _make_chain(nodes: Sequence[Comment]) -> Chain:
    speakers = {n.username.lower() for n in nodes if n.username}
    alternations = sum(
        1 for a, b in zip(nodes, nodes[1:]) if a.username.lower() != b.username.lower()
    )
    gaps = [
        abs(b.taken_at - a.taken_at)
        for a, b in zip(nodes, nodes[1:])
        if a.taken_at and b.taken_at
    ]
    latency = statistics.median(gaps) if gaps else FAST_EXCHANGE_S
    return Chain(
        nodes=tuple(nodes),
        n_speakers=len(speakers),
        alternations=alternations,
        median_latency_s=latency,
        heat=alternations / (1.0 + math.log1p(max(latency, 0.0))),
    )


def mine_chains(
    comments: Sequence[Comment],
    root_pk: str,
    *,
    min_len: int = MIN_CHAIN_LEN,
    max_window: int = MAX_WINDOW,
) -> list[Chain]:
    """Full paths plus every contiguous sub-window of length ``min_len..max_window``.

    Sub-windows matter: a six-deep flame war usually contains two or three
    usable three-beat exchanges, and we want the best window rather than the
    whole thread.
    """
    seen: set[tuple[str, ...]] = set()
    chains: list[Chain] = []

    for path in root_to_leaf_paths(comments, root_pk):
        candidates: list[Sequence[Comment]] = []
        if len(path) >= min_len:
            candidates.append(path)
        for size in range(min_len, max_window + 1):
            for start in range(0, max(0, len(path) - size + 1)):
                candidates.append(path[start : start + size])

        for window in candidates:
            key = tuple(n.pk for n in window)
            if key in seen or len(window) < min_len:
                continue
            seen.add(key)
            chain = _make_chain(window)
            if chain.n_speakers >= 2:  # one person talking to themselves is not dialogue
                chains.append(chain)

    return chains


def rank_chains(chains: Sequence[Chain], stats: CorpusStats) -> list[Chain]:
    """Hottest first, weighted by how much the thread actually engaged with it."""

    def score(chain: Chain) -> float:
        likes = [n.like_count for n in chain.nodes]
        mean_engagement = sum(
            min(1.0, math.log1p(n) / math.log1p(stats.p95_likes)) for n in likes
        ) / max(1, len(likes))
        return chain.heat * (0.35 + mean_engagement)

    return sorted(chains, key=score, reverse=True)


def rare_token_overlap(
    a: Comment,
    b: Comment,
    stats: CorpusStats,
    *,
    top_k: int = 3,
) -> float:
    """Do these two comments share the words that are rare *in this thread*?

    Common words ("mà", "rồi") are shared by everything; matching on the rare
    ones is what indicates the pair is about the same specific thing.
    """
    ta, tb = set(nz.tokens(a.text)), set(nz.tokens(b.text))
    shared = ta & tb
    if not shared:
        return 0.0
    ceiling = math.log((stats.n_docs + 1) / 0.5) if stats.n_docs else 1.0
    if ceiling <= 0:
        return 0.0
    best = sorted((stats.rarity(t) for t in shared), reverse=True)[:top_k]
    return min(1.0, sum(best) / (ceiling * top_k))


def cohesion(a: Comment, b: Comment, stats: CorpusStats) -> float:
    """How strongly *b* reads as a reply to *a*. Higher is more conversational."""
    score = 0.0
    if b.parent_pk and b.parent_pk == a.pk:
        score += 1.00  # an actual reply -- the strongest possible signal
    elif a.chain_id and b.chain_id == a.chain_id and b.order_in_chain > a.order_in_chain:
        score += 0.70
    if a.username and a.username.lower() in b.text.lower():
        score += 0.50
    score += 0.45 * rare_token_overlap(a, b, stats)
    if lex.ANAPHORA_RE.search(nz.norm(b.text)):
        score += 0.35
    if a.taken_at and b.taken_at and b.taken_at > a.taken_at:
        score += 0.25
    if a.username and b.username.lower() == a.username.lower() and b.parent_pk != a.pk:
        score -= 0.80  # the same person twice in a row is a monologue, not a chat
    return score


def cohesion_reasons(a: Comment, b: Comment, stats: CorpusStats) -> list[str]:
    """Human-readable explanation, shown in the UI's "why this order" panel."""
    out: list[str] = []
    if b.parent_pk and b.parent_pk == a.pk:
        out.append(f"trả lời trực tiếp @{a.username}")
    elif a.chain_id and b.chain_id == a.chain_id:
        out.append("cùng một chuỗi hội thoại")
    if a.username and a.username.lower() in b.text.lower():
        out.append(f"nhắc tên @{a.username}")
    if (overlap := rare_token_overlap(a, b, stats)) > 0.3:
        out.append(f"trùng từ khoá hiếm ({overlap:.2f})")
    if lex.ANAPHORA_RE.search(nz.norm(b.text)):
        out.append("có từ hồi chỉ (đúng rồi / vụ này / ý bạn là...)")
    if a.username and b.username.lower() == a.username.lower() and b.parent_pk != a.pk:
        out.append("cùng tác giả, không phải reply — bị trừ điểm")
    return out or ["không có liên kết rõ ràng"]
