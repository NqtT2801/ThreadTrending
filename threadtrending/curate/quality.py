"""Hard filters. These run before any scoring -- a rejected comment never competes.

The per-author cap is deliberately generous (3). Authors *must* be allowed to
recur: one person answering another twice is exactly the back-and-forth the
video is trying to show.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from ..models import Comment
from . import lexicons as lex
from . import normalize as nz

MIN_SYLLABLES = 3
MAX_SYLLABLES = 90
MIN_GRAPHEMES = 8
MAX_EMOJI_RATIO = 0.5
MIN_LATIN_RATIO = 0.55
NEAR_DUP_JACCARD = 0.85
PER_AUTHOR_CAP = 3
ROOT_AUTHOR_CAP = 2

REJECT_LABELS = {
    "empty": "rỗng",
    "link": "có link",
    "phone": "có số điện thoại",
    "spam": "quảng cáo",
    "emoji_only": "chỉ có emoji",
    "too_short": "quá ngắn",
    "too_long": "quá dài",
    "mention_only": "chỉ tag người khác",
    "not_vi": "không phải tiếng Việt",
    "dup": "trùng nội dung",
    "author_cap": "vượt hạn mức mỗi tác giả",
}


@dataclass(frozen=True)
class Rejection:
    comment: Comment
    reason: str

    @property
    def label(self) -> str:
        return REJECT_LABELS.get(self.reason, self.reason)


def check(comment: Comment, seen_normalized: Sequence[str]) -> str | None:
    """Return a rejection reason, or None when the comment is usable."""
    text = comment.text.strip()
    if not text:
        return "empty"
    if nz.URL_RE.search(text):
        return "link"
    if nz.PHONE_RE.search(text):
        return "phone"
    if nz.MENTION_ONLY_RE.fullmatch(text):
        return "mention_only"

    # Emoji is tested before the normalized-empty check: norm() strips emoji, so
    # an emoji-only comment would otherwise be reported as "empty".
    if nz.emoji_ratio(text) > MAX_EMOJI_RATIO:
        return "emoji_only"

    normalized = nz.norm(text)
    if not normalized:
        return "empty"
    if lex.is_spam(normalized):
        return "spam"
    if nz.syllables(text) < MIN_SYLLABLES or nz.graphemes(text) < MIN_GRAPHEMES:
        return "too_short"
    if nz.syllables(text) > MAX_SYLLABLES:
        return "too_long"
    if nz.latin_ratio(text) < MIN_LATIN_RATIO:
        return "not_vi"
    if any(nz.jaccard(normalized, other) >= NEAR_DUP_JACCARD for other in seen_normalized):
        return "dup"
    return None


def filter_comments(
    comments: Iterable[Comment],
    *,
    root_username: str = "",
) -> tuple[list[Comment], list[Rejection]]:
    """Apply every hard filter.

    Higher-engagement comments are evaluated first, so when two near-identical
    comments collide the one with more likes is the one that survives.
    """
    ordered = sorted(comments, key=lambda c: (-c.like_count, c.taken_at, c.pk))

    kept: list[Comment] = []
    rejected: list[Rejection] = []
    seen: list[str] = []
    per_author: dict[str, int] = {}
    root_author = root_username.lower()

    for comment in ordered:
        author = comment.username.lower()
        reason = check(comment, seen)
        if reason is None:
            cap = ROOT_AUTHOR_CAP if author and author == root_author else PER_AUTHOR_CAP
            if per_author.get(author, 0) >= cap:
                reason = "author_cap"

        if reason is not None:
            rejected.append(Rejection(comment, reason))
            continue

        per_author[author] = per_author.get(author, 0) + 1
        seen.append(nz.norm(comment.text))
        kept.append(comment)

    kept.sort(key=lambda c: (c.chain_id, c.depth, c.taken_at, c.pk))
    return kept, rejected
