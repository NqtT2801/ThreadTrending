"""Deterministic initials avatar, used when the real one cannot be fetched.

Pure inline SVG: no Pillow drawing, no font file lookup, no network. The hue is
derived from the username so the same person always gets the same colour across
runs, which keeps a series of videos visually consistent.
"""
from __future__ import annotations

import base64
import hashlib
import html
import unicodedata

from . import theme as T


def _initial(username: str) -> str:
    cleaned = unicodedata.normalize("NFC", (username or "").lstrip("@").strip())
    return html.escape(cleaned[:1].upper()) if cleaned else "?"


def _hue(username: str) -> int:
    digest = hashlib.sha1((username or "?").encode("utf-8")).hexdigest()
    return int(digest[:6], 16) % 360


def initials_avatar(username: str, size: int = T.AVATAR) -> str:
    """An ``image/svg+xml`` data URI, ready to drop into ``<img src>``."""
    radius = size / 2
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 {size} {size}">'
        f'<circle cx="{radius}" cy="{radius}" r="{radius}" '
        f'fill="hsl({_hue(username)}, 52%, 42%)"/>'
        f'<text x="{radius}" y="{radius + size * 0.155:.1f}" text-anchor="middle" '
        f'font-family="Inter, Segoe UI, sans-serif" font-size="{size * 0.46:.0f}" '
        f'font-weight="600" fill="#ffffff">{_initial(username)}</text>'
        "</svg>"
    )
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"
