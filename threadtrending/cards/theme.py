"""Card geometry and colour tokens.

The four numbers at the top are a hard product requirement, not a style choice:
a card is 80% of the canvas wide, at most one fifth of it tall, centred
horizontally and top-aligned with a 10% margin. Everything else in this module
is derived from them, and :func:`body_budget` is what the auto-fit search aims at.
"""
from __future__ import annotations

import base64
import functools
from pathlib import Path
from typing import Literal

from ..paths import PATHS

Theme = Literal["dark", "light"]

# --- hard constraints -------------------------------------------------------
CANVAS_W, CANVAS_H = 1080, 1920
CARD_W = 864                      # 0.80 * CANVAS_W
CARD_X = (CANVAS_W - CARD_W) // 2  # 108 -- horizontally centred
CARD_Y = CANVAS_H // 10            # 192 -- top-aligned, 10% margin
CARD_MAX_H = CANVAS_H // 5         # 384 -- HARD CAP, asserted after every render

# The banner sits inside the 192px top margin, so it never touches the card.
BANNER_W, BANNER_H = 720, 72
BANNER_X = (CANVAS_W - BANNER_W) // 2
BANNER_Y = 96

# --- card internals ---------------------------------------------------------
# Sized for a card that is 864px wide, i.e. roughly 2x a real Threads column.
PAD = 28
AVATAR = 64
GUTTER = 14
NAME_ROW_H = 34
NAME_GAP = 6
REPLY_STRIP_H = 28
ACTION_GAP = 14
ACTION_ROW_H = 26

FONT_MAX = 36
FONT_START = 30
FONT_FLOOR = 24
LINE_RATIO = 1.34

#: Chromium renders at this scale, then the PNG is resampled down to CARD_W.
#: Supersampled text is visibly cleaner once the card sits over moving video.
DEVICE_SCALE = 2

#: Bumped whenever the template or tokens change, to invalidate the PNG cache.
CSS_VERSION = "2"

TEXT_COLUMN_W = CARD_W - 2 * PAD - AVATAR - GUTTER  # 730


def chrome_height(has_reply_strip: bool) -> int:
    """Every vertical pixel a card spends on things that are not body text."""
    total = 2 * PAD + NAME_ROW_H + NAME_GAP + ACTION_GAP + ACTION_ROW_H
    if has_reply_strip:
        total += REPLY_STRIP_H
    return total


def body_budget(has_reply_strip: bool) -> int:
    """Pixels left for the comment itself: 248, or 220 with the reply strip."""
    return CARD_MAX_H - chrome_height(has_reply_strip)


TOKENS: dict[Theme, dict[str, str]] = {
    "dark": {
        # 0.92 is the sweet spot: the background still breathes through while
        # body-text contrast stays above 7:1.
        "card_bg": "rgba(16, 16, 16, 0.92)",
        "card_border": "rgba(255, 255, 255, 0.10)",
        "shadow": "0 8px 32px rgba(0, 0, 0, 0.45)",
        "name": "#FFFFFF",
        "body": "rgba(255, 255, 255, 0.95)",
        "muted": "rgba(255, 255, 255, 0.45)",
        "meta": "rgba(255, 255, 255, 0.55)",
        "reply_strip": "rgba(255, 255, 255, 0.40)",
        "verified": "#3797F0",
        "heart": "#FF3040",
        "avatar_ring": "rgba(255, 255, 255, 0.12)",
        "banner_bg": "rgba(0, 0, 0, 0.55)",
        "banner_text": "#FFFFFF",
        "banner_border": "rgba(255, 255, 255, 0.16)",
    },
    "light": {
        "card_bg": "rgba(255, 255, 255, 0.96)",
        "card_border": "rgba(0, 0, 0, 0.08)",
        "shadow": "0 8px 32px rgba(0, 0, 0, 0.28)",
        "name": "#0A0A0A",
        "body": "rgba(0, 0, 0, 0.92)",
        "muted": "rgba(0, 0, 0, 0.45)",
        "meta": "rgba(0, 0, 0, 0.55)",
        "reply_strip": "rgba(0, 0, 0, 0.42)",
        "verified": "#3797F0",
        "heart": "#FF3040",
        "avatar_ring": "rgba(0, 0, 0, 0.10)",
        "banner_bg": "rgba(255, 255, 255, 0.92)",
        "banner_text": "#0A0A0A",
        "banner_border": "rgba(0, 0, 0, 0.10)",
    },
}

#: (file stem, css font-weight). Latin and Vietnamese subsets are both required:
#: the Vietnamese subset only carries the precomposed Vietnamese codepoints, so
#: plain ASCII letters still come from the Latin file.
FONT_FILES: tuple[tuple[str, int], ...] = (
    ("inter-latin-400-normal", 400),
    ("inter-vietnamese-400-normal", 400),
    ("inter-latin-600-normal", 600),
    ("inter-vietnamese-600-normal", 600),
    ("inter-vietnamese-500-normal", 500),
    ("inter-vietnamese-700-normal", 700),
)


@functools.lru_cache(maxsize=1)
def font_face_css() -> str:
    """``@font-face`` rules with the fonts inlined as data URIs.

    Data URIs are mandatory rather than tidy: the card page is loaded with
    ``page.set_content()`` and therefore has no base URL, so relative ``url()``
    and ``file://`` references do not resolve.
    """
    rules: list[str] = []
    for stem, weight in FONT_FILES:
        path = PATHS.fonts / f"{stem}.woff2"
        if not path.is_file():
            continue
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        rules.append(
            "@font-face{font-family:'TT';font-style:normal;"
            f"font-weight:{weight};font-display:block;"
            f"src:url(data:font/woff2;base64,{encoded}) format('woff2');}}"
        )
    return "".join(rules)


def has_bundled_fonts() -> bool:
    return any((PATHS.fonts / f"{stem}.woff2").is_file() for stem, _ in FONT_FILES)


def tokens(theme: Theme) -> dict[str, str]:
    return TOKENS.get(theme, TOKENS["dark"])
