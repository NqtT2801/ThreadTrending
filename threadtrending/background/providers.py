"""Free stock-video providers: Pexels and Pixabay.

Both are free and both expose a portrait filter. Portrait is strongly preferred:
cropping 16:9 down to 9:16 throws away 71% of the frame width, which usually
takes the subject with it.

Rate limits are tracked in the ledger rather than in memory, so they survive a
Streamlit rerun. At 80% of a provider's hourly budget the selector switches to
the other one.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol, Sequence

import httpx

log = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(20.0, connect=10.0)

#: Pexels publishes 200 requests/hour; Pixabay is informally ~100/minute.
HOURLY_BUDGET = {"pexels": 200, "pixabay": 500}
SWITCH_AT = 0.80


@dataclass(frozen=True)
class StockClip:
    provider: str
    video_id: str
    file_url: str
    width: int
    height: int
    duration: float
    fps: float
    page_url: str = ""
    author: str = ""
    author_url: str = ""

    @property
    def is_portrait(self) -> bool:
        return self.height > self.width

    @property
    def key(self) -> tuple[str, str]:
        return (self.provider, self.video_id)


class Provider(Protocol):
    name: str

    def search(self, query: str, *, per_page: int, page: int,
               portrait: bool) -> list[StockClip]: ...


class PexelsProvider:
    name = "pexels"
    BASE = "https://api.pexels.com/v1/videos/search"

    def __init__(self, api_key: str) -> None:
        self._key = api_key

    def search(self, query: str, *, per_page: int = 60, page: int = 1,
               portrait: bool = True) -> list[StockClip]:
        params = {
            "query": query,
            "per_page": min(80, per_page),
            "page": page,
            "size": "medium",
        }
        if portrait:
            params["orientation"] = "portrait"

        with httpx.Client(timeout=TIMEOUT) as client:
            response = client.get(
                self.BASE, params=params, headers={"Authorization": self._key}
            )
        if response.status_code == 429:
            log.warning("Pexels rate limited")
            return []
        response.raise_for_status()
        return [c for c in map(self._to_clip, response.json().get("videos", [])) if c]

    def _to_clip(self, raw: dict[str, Any]) -> StockClip | None:
        files = [
            f for f in raw.get("video_files", [])
            if f.get("quality") in ("hd", "uhd") and f.get("link")
            and (f.get("file_type") or "").endswith("mp4")
        ]
        if not files:
            return None
        # Smallest file that still clears 1080 on the short edge: 4K masters are
        # a slow download for a background that gets cropped to 1080x1920 anyway.
        files.sort(key=lambda f: (f.get("width") or 0) * (f.get("height") or 0))
        best = next(
            (f for f in files if min(f.get("width", 0), f.get("height", 0)) >= 1080),
            files[-1],
        )
        user = raw.get("user") or {}
        return StockClip(
            provider=self.name,
            video_id=str(raw.get("id")),
            file_url=best["link"],
            width=int(best.get("width") or raw.get("width") or 0),
            height=int(best.get("height") or raw.get("height") or 0),
            duration=float(raw.get("duration") or 0),
            fps=float(best.get("fps") or 0),
            page_url=raw.get("url", ""),
            author=user.get("name", ""),
            author_url=user.get("url", ""),
        )


class PixabayProvider:
    name = "pixabay"
    BASE = "https://pixabay.com/api/videos/"

    def __init__(self, api_key: str) -> None:
        self._key = api_key

    def search(self, query: str, *, per_page: int = 60, page: int = 1,
               portrait: bool = True) -> list[StockClip]:
        params: dict[str, Any] = {
            "key": self._key,
            "q": query,
            "per_page": min(200, per_page),
            "page": page,
            "safesearch": "true",
            "video_type": "film",
        }
        with httpx.Client(timeout=TIMEOUT) as client:
            response = client.get(self.BASE, params=params)
        if response.status_code == 429:
            log.warning("Pixabay rate limited")
            return []
        response.raise_for_status()

        clips = [c for c in map(self._to_clip, response.json().get("hits", [])) if c]
        # Pixabay has no orientation filter, so it is applied client-side.
        return [c for c in clips if c.is_portrait] if portrait else clips

    def _to_clip(self, raw: dict[str, Any]) -> StockClip | None:
        videos = raw.get("videos") or {}
        best = None
        for quality in ("large", "medium", "small"):
            entry = videos.get(quality) or {}
            if entry.get("url") and entry.get("width"):
                best = entry
                if min(entry["width"], entry.get("height", 0)) >= 1080:
                    break
        if not best:
            return None
        return StockClip(
            provider=self.name,
            video_id=str(raw.get("id")),
            file_url=best["url"],
            width=int(best.get("width") or 0),
            height=int(best.get("height") or 0),
            duration=float(raw.get("duration") or 0),
            fps=0.0,  # not reported; probed after download
            page_url=raw.get("pageURL", ""),
            author=raw.get("user", ""),
            author_url=f"https://pixabay.com/users/{raw.get('user', '')}/",
        )


def build_providers(pexels_key: str | None, pixabay_key: str | None) -> list[Provider]:
    providers: list[Provider] = []
    if pexels_key:
        providers.append(PexelsProvider(pexels_key))
    if pixabay_key:
        providers.append(PixabayProvider(pixabay_key))
    return providers


def budget_used(ledger, provider_name: str) -> float:
    """Fraction of this provider's hourly budget already spent."""
    budget = HOURLY_BUDGET.get(provider_name, 200)
    return ledger.calls_in_last_hour(provider_name) / budget


def order_by_headroom(providers: Sequence[Provider], ledger) -> list[Provider]:
    """Least-used provider first; anything past 80% goes to the back."""
    return sorted(providers, key=lambda p: budget_used(ledger, p.name))
