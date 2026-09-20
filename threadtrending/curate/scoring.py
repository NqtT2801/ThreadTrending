"""Narrative-role scorers tuned for Vietnamese social-media text.

Every score returns roughly 0..1. Engagement is normalized against the *95th
percentile* of the thread, not its maximum: one viral reply would otherwise
flatten every other comment to nearly zero.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Sequence

from ..models import Comment
from . import lexicons as lex
from . import normalize as nz

#: Google vi-VN neural voices read about five syllables per second at rate 1.0.
SYLLABLES_PER_SECOND = 5.0
#: Fixed overhead per utterance: leading breath plus trailing decay.
UTTERANCE_OVERHEAD_S = 0.35

PUNCT_BURST_RE = re.compile(r"!{2,}|\?{2,}|\?!")
NUMERIC_RE = re.compile(
    r"\d+(?:[.,:/]\d+)?|(?<![0-9a-z])(?:gio|ngay|thang|nam|tuan|phut)(?![0-9a-z])"
)
CONNECTIVE_RE = re.compile(
    r"(?<![0-9a-z])(?:vi|nen|ma|thi|roi|sau do|do do|nhung|tuc la)(?![0-9a-z])"
)


def estimate_seconds(text: str, rate: float = 1.0) -> float:
    """Predicted TTS duration. The single knob the duration budget is built on."""
    return nz.syllables(text) / SYLLABLES_PER_SECOND / max(rate, 0.1) + UTTERANCE_OVERHEAD_S


def _saturate(x: float, k: float) -> float:
    """Diminishing returns: one strong marker counts, five do not count five times."""
    return 1.0 - math.exp(-max(x, 0.0) / k)


def band(x: float, lo: float, hi: float, slope: float) -> float:
    """1.0 inside [lo, hi], decaying linearly outside it."""
    if lo <= x <= hi:
        return 1.0
    distance = (lo - x) if x < lo else (x - hi)
    return max(0.0, 1.0 - slope * distance)


def _percentile(values: Sequence[int], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return float(ordered[idx])


@dataclass
class CorpusStats:
    """Thread-local normalization constants plus an IDF table."""

    p95_likes: float = 10.0
    p95_replies: float = 1.0
    idf: dict[str, float] = field(default_factory=dict)
    n_docs: int = 0

    @classmethod
    def build(cls, comments: Sequence[Comment]) -> "CorpusStats":
        if not comments:
            return cls()
        df: Counter[str] = Counter()
        for comment in comments:
            df.update(set(nz.tokens(comment.text)))
        n = len(comments)
        return cls(
            p95_likes=max(10.0, _percentile([c.like_count for c in comments], 0.95)),
            p95_replies=max(1.0, _percentile([c.reply_count for c in comments], 0.95)),
            idf={t: math.log((n + 1) / (c + 0.5)) for t, c in df.items()},
            n_docs=n,
        )

    def rarity(self, token: str) -> float:
        """IDF of a token; unseen tokens get the maximum value for this corpus."""
        if token in self.idf:
            return self.idf[token]
        return math.log((self.n_docs + 1) / 0.5) if self.n_docs else 0.0


@dataclass(frozen=True)
class Features:
    shock: float
    explain: float
    counter: float
    reaction: float
    punchline: float
    engagement: float
    brevity: float
    entity: float
    depth_bonus: float
    est_seconds: float
    syllables: int


def engagement(comment: Comment, stats: CorpusStats) -> float:
    return min(1.0, math.log1p(comment.like_count) / math.log1p(stats.p95_likes))


def shock_score(comment: Comment, stats: CorpusStats) -> float:
    text, n = comment.text, nz.norm(comment.text)
    return (
        0.34 * _saturate(lex.lex_hits(n, lex.SHOCK_RE), k=3.0)
        + 0.14 * min(len(PUNCT_BURST_RE.findall(text)), 4) / 4
        + 0.08 * nz.caps_run_ratio(text)
        + 0.10 * min(lex.count_emoji(text, lex.SHOCK_EMOJI), 3) / 3
        + 0.12 * band(nz.syllables(text), 4, 18, slope=0.06)
        + 0.18 * engagement(comment, stats)
        + 0.04 * (1.0 if text.rstrip().endswith(("?", "!")) else 0.0)
    )


def explain_score(comment: Comment, stats: CorpusStats) -> float:
    text, n = comment.text, nz.norm(comment.text)
    return (
        0.34 * _saturate(lex.lex_hits(n, lex.EXPLAIN_RE), k=2.5)
        + 0.22 * band(nz.syllables(text), 14, 42, slope=0.035)
        + 0.12 * min(len(NUMERIC_RE.findall(n)), 3) / 3
        + 0.10 * (1.0 - nz.emoji_ratio(text))
        + 0.12 * min(n.count(",") + len(CONNECTIVE_RE.findall(n)), 4) / 4
        + 0.10 * engagement(comment, stats)
    )


def counter_score(comment: Comment, stats: CorpusStats) -> float:
    text, n = comment.text, nz.norm(comment.text)
    if nz.syllables(text) < 6:
        return 0.0  # a real rebuttal needs room to state itself
    return (
        0.44 * _saturate(lex.lex_hits(n, lex.COUNTER_RE), k=2.0)
        + 0.18 * _saturate(lex.lex_hits(n, lex.EXPLAIN_RE), k=3.0)
        + 0.16 * band(nz.syllables(text), 8, 36, slope=0.04)
        + 0.12 * engagement(comment, stats)
        + 0.10 * (1.0 - nz.emoji_ratio(text))
    )


def reaction_score(comment: Comment, stats: CorpusStats) -> float:
    text, n = comment.text, nz.norm(comment.text)
    return (
        0.40 * _saturate(lex.lex_hits(n, lex.REACTION_RE), k=2.0)
        + 0.16 * min(lex.count_emoji(text, lex.LAUGH_EMOJI), 2) / 2
        + 0.26 * band(nz.syllables(text), 3, 14, slope=0.07)
        + 0.18 * engagement(comment, stats)
    )


def brevity(comment: Comment) -> float:
    return band(nz.syllables(comment.text), 3, 16, slope=0.055)


def entity_score(comment: Comment) -> float:
    """Proper nouns and numbers: a proxy for "this comment names the subject"."""
    words = comment.text.split()
    if not words:
        return 0.0
    capitalised = sum(1 for w in words[1:] if w[:1].isupper())
    digits = sum(1 for w in words if any(ch.isdigit() for ch in w))
    return min(1.0, (capitalised + digits) / max(3.0, len(words) / 3))


def punchline_score(comment: Comment, stats: CorpusStats) -> float:
    return (
        0.40 * reaction_score(comment, stats)
        + 0.25 * shock_score(comment, stats)
        + 0.25 * engagement(comment, stats)
        + 0.10 * brevity(comment)
    )


def features(comment: Comment, stats: CorpusStats, rate: float = 1.0) -> Features:
    return Features(
        shock=shock_score(comment, stats),
        explain=explain_score(comment, stats),
        counter=counter_score(comment, stats),
        reaction=reaction_score(comment, stats),
        punchline=punchline_score(comment, stats),
        engagement=engagement(comment, stats),
        brevity=brevity(comment),
        entity=entity_score(comment),
        depth_bonus=min(1.0, comment.depth / 3.0),
        est_seconds=estimate_seconds(comment.text, rate),
        syllables=nz.syllables(comment.text),
    )
