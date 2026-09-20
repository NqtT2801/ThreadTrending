"""Render comment cards to transparent PNGs, never taller than 384px.

The height cap is absolute, so fitting happens in two stages:

1. **Binary-search the font size** between ``FONT_FLOOR`` and ``FONT_MAX`` for
   the largest size whose laid-out card still fits. Four in-page measurements.
2. **Paginate** when even the floor overflows. Page breaks are taken from
   :func:`..curate.tts_text.chunk_for_tts`, the same function that splits the
   audio, so a card turns exactly when the voice reaches that point.

Chunking has to happen *before* fitting, not after: audio is synthesized per
page, so deciding page boundaries after synthesis would desync the two.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from PIL import Image

from ..curate.tts_text import chunk_for_tts
from ..errors import CardOverflow
from ..models import CardModel, Page
from ..paths import PATHS
from . import theme as T
from .fallback_avatar import initials_avatar
from .template import render_banner_html, render_card_html

log = logging.getLogger(__name__)

#: Syllables per page before we even try to fit. Roughly 11 seconds of speech.
PAGE_MAX_SYLLABLES = 55


@dataclass(frozen=True)
class FittedCard:
    page: Page
    font_px: int
    png: Path


class CardRenderer:
    """Stateful renderer bound to one Playwright page.

    The page is created once and reused; ``window.__fit`` then does layout
    measurement in-browser, which is what keeps the binary search cheap.
    """

    def __init__(self, browser_manager: Any, *, theme: T.Theme = "dark",
                 cache_dir: Path | None = None) -> None:
        self._bm = browser_manager
        self._theme: T.Theme = theme
        self._cache_dir = cache_dir or PATHS.cache_cards
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._page: Any = None
        self._loaded_signature: str | None = None

    # --- page plumbing ----------------------------------------------------
    def _ensure_page(self) -> Any:
        if self._page is None:
            context = self._bm.render_context(T.DEVICE_SCALE)
            self._page = self._bm.call(context.new_page)
        return self._page

    def _load(self, model: CardModel, font_px: int, page_label: str) -> None:
        """Load the card HTML, but only when something other than text changed."""
        page = self._ensure_page()
        signature = _signature(model, page_label)
        if signature == self._loaded_signature:
            return
        html = render_card_html(
            username=model.username,
            body=model.text,
            avatar_src=model.avatar_data_uri or initials_avatar(model.username),
            is_verified=model.is_verified,
            timestamp=model.timestamp_label,
            like_count=model.like_count,
            reply_count=model.reply_count,
            reply_to=model.reply_to_username,
            theme=self._theme,
            font_px=font_px,
            page_label=page_label,
        )
        self._bm.call(page.set_content, html, wait_until="load")
        self._bm.call(page.evaluate, "document.fonts.ready")
        self._loaded_signature = signature

    def measure(self, model: CardModel, text: str, font_px: int,
                page_label: str = "") -> float:
        """Laid-out card height in CSS pixels for ``text`` at ``font_px``."""
        self._load(model, font_px, page_label)
        page = self._ensure_page()
        body_html = _escape_for_js(text)
        return float(
            self._bm.call(
                page.evaluate,
                "([html, px]) => window.__fit(html, px)",
                [body_html, font_px],
            )
        )

    # --- fitting ----------------------------------------------------------
    def fit_font(self, model: CardModel, text: str, page_label: str = "") -> int | None:
        """Largest integer font size that fits, or None when pagination is needed."""
        low, high, best = T.FONT_FLOOR, T.FONT_MAX, None
        while low <= high:
            mid = (low + high) // 2
            if self.measure(model, text, mid, page_label) <= T.CARD_MAX_H:
                best, low = mid, mid + 1
            else:
                high = mid - 1
        return best

    def paginate(self, model: CardModel, text: str) -> list[Page]:
        """Split ``text`` into pages that each fit at ``FONT_FLOOR``."""
        chunks = chunk_for_tts(text, max_syllables=PAGE_MAX_SYLLABLES)
        if not chunks:
            return [Page(text=text, index=0, of=1)]

        pages: list[str] = []
        current = ""
        queue = list(chunks)

        while queue:
            chunk = queue.pop(0)
            trial = f"{current} {chunk}".strip() if current else chunk
            if self.measure(model, trial, T.FONT_FLOOR, "0/0") <= T.CARD_MAX_H:
                current = trial
                continue
            if current:
                pages.append(current)
                current = ""
                queue.insert(0, chunk)
                continue
            # A single chunk that overflows on its own: split it by hand.
            head, tail = self._hard_split(model, chunk)
            pages.append(head)
            if tail:
                queue.insert(0, tail)

        if current:
            pages.append(current)

        total = len(pages)
        return [Page(text=t, index=i, of=total) for i, t in enumerate(pages)]

    def _hard_split(self, model: CardModel, text: str) -> tuple[str, str]:
        """Binary-search the longest word-aligned prefix that fits at the floor."""
        words = text.split()
        if len(words) <= 1:
            return text, ""
        low, high, best = 1, len(words), 1
        while low <= high:
            mid = (low + high) // 2
            if self.measure(model, " ".join(words[:mid]), T.FONT_FLOOR, "0/0") <= T.CARD_MAX_H:
                best, low = mid, mid + 1
            else:
                high = mid - 1
        return " ".join(words[:best]), " ".join(words[best:])

    def layout(self, model: CardModel) -> tuple[list[Page], int]:
        """Pages plus the single font size to use for all of them.

        One size for the whole comment is deliberate: letting each page pick its
        own makes the card visibly pulse between pages, which reads as a bug.
        """
        single = self.fit_font(model, model.text)
        if single is not None:
            return [Page(text=model.text, index=0, of=1)], single

        pages = self.paginate(model, model.text)
        label = f"{len(pages)}/{len(pages)}"
        sizes = [self.fit_font(model, p.text, label) for p in pages]
        if any(s is None for s in sizes):
            raise CardOverflow(
                f"Không thể xếp vừa card cho bình luận của @{model.username} "
                f"({len(model.text)} ký tự) dù đã phân trang."
            )
        return pages, min(s for s in sizes if s is not None)

    # --- rendering --------------------------------------------------------
    def render(self, model: CardModel, page: Page, font_px: int,
               out_dir: Path | None = None) -> Path:
        """Screenshot one page to a transparent PNG resampled to exactly 864px."""
        page_label = f"{page.index + 1}/{page.of}" if page.of > 1 else ""
        key = _cache_key(model, page, font_px, self._theme)
        target = (out_dir or self._cache_dir) / f"card_{key}.png"
        if target.exists():
            return target

        model_for_page = CardModel(**{**model.__dict__, "text": page.text})
        self._load(model_for_page, font_px, page_label)
        browser_page = self._ensure_page()
        element = self._bm.call(browser_page.query_selector, "#card")
        if element is None:
            raise CardOverflow("Không tìm thấy #card trong template.")

        target.parent.mkdir(parents=True, exist_ok=True)
        raw = self._bm.call(element.screenshot, omit_background=True, type="png")
        _write_downscaled(raw, target)

        width, height = Image.open(target).size
        if width != T.CARD_W or height > T.CARD_MAX_H:
            target.unlink(missing_ok=True)
            raise CardOverflow(
                f"Card {target.name} có kích thước {width}x{height}, "
                f"yêu cầu {T.CARD_W}x<={T.CARD_MAX_H}"
            )
        return target

    def render_all(self, model: CardModel, out_dir: Path | None = None) -> list[FittedCard]:
        pages, font_px = self.layout(model)
        return [
            FittedCard(page=p, font_px=font_px, png=self.render(model, p, font_px, out_dir))
            for p in pages
        ]

    def render_banner(self, title: str, out_path: Path,
                      subtitle: str = "đang trending trên Threads") -> Path:
        html = render_banner_html(title=title, subtitle=subtitle, theme=self._theme)
        page = self._ensure_page()
        self._bm.call(page.set_content, html, wait_until="load")
        self._bm.call(page.evaluate, "document.fonts.ready")
        self._loaded_signature = None  # banner replaced the card markup

        element = self._bm.call(page.query_selector, "#banner")
        raw = self._bm.call(element.screenshot, omit_background=True, type="png")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        _write_downscaled(raw, out_path, target_width=T.BANNER_W)
        return out_path


def _write_downscaled(raw: bytes, target: Path, target_width: int = T.CARD_W) -> None:
    """Chromium renders at DEVICE_SCALE; store the final-resolution PNG.

    Writing the cache at output resolution means ffmpeg needs no scale filter and
    the overlay is a straight alpha blend.
    """
    import io

    with Image.open(io.BytesIO(raw)) as image:
        image = image.convert("RGBA")
        if image.width != target_width:
            height = round(image.height * target_width / image.width)
            image = image.resize((target_width, height), Image.LANCZOS)
        image.save(target, format="PNG", optimize=True)


def _escape_for_js(text: str) -> str:
    from .template import escape_body

    return escape_body(text)


def _signature(model: CardModel, page_label: str) -> str:
    """Everything that forces a full page reload (text is set via __fit instead)."""
    return json.dumps(
        [model.username, model.avatar_data_uri[:64], model.is_verified,
         model.timestamp_label, model.like_count, model.reply_count,
         model.reply_to_username, page_label],
        ensure_ascii=False,
    )


def _cache_key(model: CardModel, page: Page, font_px: int, theme: str) -> str:
    payload = json.dumps(
        {
            "u": model.username, "t": page.text, "v": model.is_verified,
            "ts": model.timestamp_label, "lk": model.like_count,
            "rp": model.reply_count, "rt": model.reply_to_username,
            "pg": [page.index, page.of], "f": font_px, "th": theme,
            "av": hashlib.sha1(model.avatar_data_uri.encode()).hexdigest()[:12],
            "css": T.CSS_VERSION, "dsf": T.DEVICE_SCALE,
        },
        ensure_ascii=False, sort_keys=True,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:20]
