"""The search-term pool that enforces "energetic, not chill".

Two mechanisms, because a query alone is not enough:

* This pool contains only high-motion subjects. Sunsets, candles and slow ocean
  footage are simply absent.
* :data:`CALM_SLUG_RE` rejects results whose own page slug advertises calm, which
  catches the "relaxing city timelapse" that a query for "city" drags in.

The pool is rotated by run count rather than sampled uniformly, so successive
videos draw from different regions of the corpus instead of re-rolling the same
popular query.
"""
from __future__ import annotations

import random
import re

ENERGETIC_POOL: tuple[str, ...] = (
    "parkour rooftop", "street dance crew", "skateboarding trick", "bmx jump",
    "hyperlapse traffic night", "neon city night walk", "drone flythrough city",
    "esports gaming setup", "crowd concert jumping", "race car drift",
    "motorcycle ride first person", "basketball dunk", "boxing training",
    "sprint running track", "surfing big wave", "snowboarding powder",
    "confetti explosion", "sparks welding", "fireworks close up",
    "abstract fluid motion", "glitch digital motion", "led light tunnel",
    "subway train speed", "city crossing timelapse", "stage lights concert",
    "graffiti spray painting", "climbing wall speed", "roller coaster pov",
    "drum solo closeup", "car headlights night rain",
)

#: Slugs that mean the clip is calm no matter what we searched for.
CALM_SLUG_RE = re.compile(
    r"(calm|relax|meditat|slow|peaceful|serene|aesthetic|ambient|sleep|"
    r"gentle|soothing|tranquil|lofi|study)",
    re.I,
)

#: Vietnamese topic words are useless against an English stock corpus, so the
#: topic is only injected when it contains a whole ASCII word such as a brand or
#: game name. Matching loose letter runs instead would slice "Tuyển" into "Tuy"
#: and search the stock library for a fragment.
TOPIC_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]{2,}$")
#: Words that are ASCII but say nothing about imagery.
TOPIC_STOPWORDS = frozenset({
    "the", "and", "for", "you", "with", "live", "new", "hot", "top", "net",
    "com", "vietnam", "viet", "nam", "update", "news", "video", "clip",
    # Vietnamese function words that happen to be pure ASCII once unaccented.
    "nay", "hom", "gia", "cua", "khi", "duoc", "nguoi", "dan", "mot", "cho",
    "vao", "ban", "the", "hoa", "chi", "tin", "bao", "anh", "cac", "con",
})


def rotate(run_count: int, *, seed: int | None = None) -> str:
    """Pick a query, advancing through the pool as runs accumulate."""
    offset = run_count % len(ENERGETIC_POOL)
    rng = random.Random(seed if seed is not None else run_count)
    # Jitter within a small window so the order is not perfectly predictable
    # while still sweeping the whole pool.
    window = ENERGETIC_POOL[offset:] + ENERGETIC_POOL[:offset]
    return rng.choice(window[: max(4, len(window) // 4)])


def with_topic(query: str, trend_name: str) -> str:
    """Append a whole ASCII word from the trend, when there is a usable one.

    All-caps tokens are preferred: in Vietnamese trend names the ASCII words
    that actually describe imagery are brands, games and acronyms (PUBG, VTV,
    BTC), and those are written in caps. A title-case token is the fallback --
    it might be "Concert", but it might equally be "Trung".
    """
    usable = [
        token
        for raw in (trend_name or "").split()
        if (token := raw.strip(".,!?:;\"'()[]#"))
        and TOPIC_TOKEN_RE.match(token)
        and token.lower() not in TOPIC_STOPWORDS
    ]
    if not usable:
        return query
    acronyms = [t for t in usable if t.isupper()]
    return f"{query} {(acronyms or usable)[0].lower()}"


def is_calm(*texts: str) -> bool:
    return any(CALM_SLUG_RE.search(t or "") for t in texts)
