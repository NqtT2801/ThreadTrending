"""Parse the two Threads URL shapes we accept.

A *trend* URL (``/search?q=...&serp_type=trends&trend_fbid=...``) needs a logged-in
session; a *post* permalink (``/@user/post/<code>``) does not. Accepting both means
a broken SERP scrape never blocks the rest of the pipeline -- the user can paste a
permalink instead.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qs, unquote, urlparse

THREADS_HOSTS = {"threads.com", "www.threads.com", "threads.net", "www.threads.net"}

# /@username/post/<code>  -- code is Instagram's base64-ish shortcode
POST_PATH_RE = re.compile(r"/@(?P<user>[^/]+)/post/(?P<code>[A-Za-z0-9_-]+)")
BARE_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{8,24}$")


@dataclass(frozen=True)
class TrendTarget:
    kind: Literal["trend", "post"]
    url: str
    query: str = ""
    trend_fbid: str = ""
    username: str = ""
    post_code: str = ""

    @property
    def display_name(self) -> str:
        """What goes on the banner."""
        return self.query or (f"@{self.username}" if self.username else "Threads")

    @property
    def needs_login(self) -> bool:
        return self.kind == "trend"

    def permalink(self) -> str:
        if self.kind != "post":
            raise ValueError("permalink() is only defined for post targets")
        return f"https://www.threads.com/@{self.username}/post/{self.post_code}"


class InvalidUrl(ValueError):
    pass


def parse(raw: str) -> TrendTarget:
    """Turn user input into a target. Raises :class:`InvalidUrl` on anything else."""
    text = (raw or "").strip()
    if not text:
        raise InvalidUrl("Chưa nhập link.")

    if BARE_CODE_RE.fullmatch(text):
        # A bare shortcode still resolves; the username is filled in after fetch.
        return TrendTarget(kind="post", url=f"https://www.threads.com/t/{text}", post_code=text)

    if not text.startswith(("http://", "https://")):
        text = "https://" + text

    parts = urlparse(text)
    host = parts.netloc.lower()
    if host not in THREADS_HOSTS:
        raise InvalidUrl(f"Không phải link Threads: {parts.netloc or raw}")

    if m := POST_PATH_RE.search(parts.path):
        return TrendTarget(
            kind="post",
            url=text,
            username=unquote(m.group("user")),
            post_code=m.group("code"),
        )

    if parts.path.rstrip("/").endswith("/search"):
        qs = parse_qs(parts.query)
        query = (qs.get("q") or [""])[0]
        if not query:
            raise InvalidUrl("Link search thiếu tham số q=.")
        return TrendTarget(
            kind="trend",
            url=text,
            query=unquote(query),
            trend_fbid=(qs.get("trend_fbid") or [""])[0],
        )

    raise InvalidUrl(
        "Link phải là trang trend (/search?q=...&serp_type=trends) "
        "hoặc permalink bài viết (/@user/post/<code>)."
    )


def canonical_permalink(username: str, code: str) -> str:
    return f"https://www.threads.com/@{username}/post/{code}"
