"""Streamed, resumable download with a content hash.

The hash is what catches the same footage mirrored under different ids on
different providers -- an id-only ledger would happily hand you the same clip
twice.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Callable

import httpx

log = logging.getLogger(__name__)

CHUNK = 1 << 18  # 256 KiB
TIMEOUT = httpx.Timeout(60.0, connect=15.0)
MAX_BYTES = 220 * 1024 * 1024


def download(
    url: str,
    target: Path,
    *,
    progress: Callable[[float], None] | None = None,
) -> str:
    """Fetch ``url`` to ``target`` and return its sha256.

    An existing complete file is reused and re-hashed rather than re-fetched.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size > 1024:
        return sha256_of(target)

    partial = target.with_suffix(target.suffix + ".part")
    digest = hashlib.sha256()
    written = 0

    with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length") or 0)
            if total and total > MAX_BYTES:
                raise ValueError(f"Clip quá lớn ({total / 1e6:.0f} MB)")

            with partial.open("wb") as handle:
                for chunk in response.iter_bytes(CHUNK):
                    handle.write(chunk)
                    digest.update(chunk)
                    written += len(chunk)
                    if written > MAX_BYTES:
                        raise ValueError("Clip vượt quá giới hạn kích thước")
                    if progress and total:
                        progress(min(1.0, written / total))

    partial.replace(target)
    if progress:
        progress(1.0)
    log.info("downloaded %s (%.1f MB)", target.name, written / 1e6)
    return digest.hexdigest()


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()
