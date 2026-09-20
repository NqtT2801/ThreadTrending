"""Fetch profile pictures and inline them as data URIs.

Threads serves avatars from ``scontent-*.cdninstagram.com``, which 403s a bare
hotlink and signs its URLs with an expiry. Three tiers:

1. ``context.request`` -- Playwright's ``APIRequestContext`` shares the session
   cookies and is **not** subject to CORS. An in-page ``fetch()`` is blocked at
   the CDN origin and ``mode:'no-cors'`` returns an opaque body, so this is the
   only in-browser option that yields actual bytes.
2. plain httpx with a browser UA and a Referer.
3. a deterministic initials avatar.

Cached by ``sha1(username)``, not by URL: the URL expires, the bytes do not,
which also makes a rerun work offline.
"""
from __future__ import annotations

import base64
import hashlib
import logging
from pathlib import Path
from typing import Any

import httpx

from ..cards.fallback_avatar import initials_avatar
from ..paths import PATHS

log = logging.getLogger(__name__)

HEADERS = {
    "Referer": "https://www.threads.com/",
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
}
MAX_BYTES = 2_000_000
MIN_BYTES = 128
TIMEOUT = httpx.Timeout(15.0, connect=8.0)

PNG_MAGIC = bytes([0x89, 0x50, 0x4E, 0x47])
JPEG_MAGIC = bytes([0xFF, 0xD8, 0xFF])


def _cache_file(username: str) -> Path:
    digest = hashlib.sha1((username or "?").encode("utf-8")).hexdigest()[:20]
    return PATHS.cache_avatars / f"{digest}.img"


def _media_type(payload: bytes) -> str:
    if payload.startswith(PNG_MAGIC):
        return "image/png"
    if payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _data_uri(payload: bytes) -> str:
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:{_media_type(payload)};base64,{encoded}"


def _via_playwright(context: Any, url: str) -> bytes | None:
    try:
        response = context.request.get(url, headers=HEADERS, timeout=15_000)
        if response.ok:
            body = response.body()
            if body and MIN_BYTES < len(body) < MAX_BYTES:
                return body
    except Exception as exc:
        log.debug("playwright avatar fetch failed: %s", exc)
    return None


def _via_httpx(url: str) -> bytes | None:
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
            response = client.get(url, headers=HEADERS)
        if response.status_code == 200 and MIN_BYTES < len(response.content) < MAX_BYTES:
            return response.content
    except Exception as exc:
        log.debug("httpx avatar fetch failed: %s", exc)
    return None


def fetch_avatar(browser_manager: Any, username: str, url: str) -> str:
    """Return a data URI for this user's avatar, falling back to initials."""
    cached = _cache_file(username)
    if cached.is_file() and cached.stat().st_size > MIN_BYTES:
        return _data_uri(cached.read_bytes())

    payload: bytes | None = None
    if url:
        try:
            context = browser_manager.scrape_context(headless=True)
            payload = browser_manager.call(_via_playwright, context, url)
        except Exception as exc:
            log.debug("avatar via browser unavailable: %s", exc)
        if payload is None:
            payload = _via_httpx(url)

    if payload:
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(payload)
        return _data_uri(payload)

    return initials_avatar(username)
