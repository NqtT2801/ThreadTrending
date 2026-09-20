"""Choose a background clip: energetic, portrait, and never used before.

The pipeline, in order:

1. rotate an energetic query (:mod:`.queries`)
2. search the provider with the most rate-limit headroom
3. drop anything the ledger has already seen, and anything whose own page slug
   advertises calm
4. prefer clips longer than the video so the loop seam never shows
5. **reserve** the winner in the ledger before downloading, so two runs cannot
   claim it at once
6. download, hash, and reject a hash we already hold (same footage, two providers)
7. run the motion gate -- this is where "sôi nổi, không chill" is actually enforced
8. confirm on success; release the reservation on any failure

Exhaustion is handled rather than raised: recycle clips older than 180 days,
then fall back to whatever the user dropped in ``assets/backgrounds``.
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from ..errors import NoBackgroundAvailable
from ..ffmpeg_locator import FfmpegTools
from ..models import BackgroundClip
from ..paths import PATHS
from . import queries as Q
from .downloader import download, sha256_of
from .ledger import Ledger
from .motion import MOTION_THRESHOLD, motion_score
from .providers import Provider, StockClip, order_by_headroom
from ..render.probe import media_info

log = logging.getLogger(__name__)

MAX_DOWNLOADS_PER_RUN = 4
MAX_QUERY_ROTATIONS = 3
DURATION_MARGIN_S = 2.0


@dataclass
class Selection:
    clip: BackgroundClip
    ledger_row_id: int | None
    recycled: bool = False
    warnings: tuple[str, ...] = ()


def select_background(
    tools: FfmpegTools,
    ledger: Ledger,
    providers: Sequence[Provider],
    *,
    run_id: str,
    needed_seconds: float,
    trend_name: str = "",
    query_override: str = "",
    motion_threshold: float = MOTION_THRESHOLD,
    progress: Callable[[str], None] | None = None,
) -> Selection:
    """Return a downloaded, verified clip and its (reserved) ledger row."""

    def say(message: str) -> None:
        log.info(message)
        if progress:
            progress(message)

    warnings: list[str] = []
    downloads = 0
    run_count = ledger.run_count()
    # Safety net for an over-strict threshold: remember the liveliest clip that
    # was rejected, so a mis-calibrated gate degrades to "least chill available"
    # instead of "no background at all".
    best_rejected: tuple[float, BackgroundClip, int] | None = None

    if providers:
        for rotation in range(MAX_QUERY_ROTATIONS):
            query = query_override or Q.with_topic(
                Q.rotate(run_count + rotation), trend_name
            )
            for provider in order_by_headroom(providers, ledger):
                say(f"Tìm video nền: '{query}' trên {provider.name}")
                candidates = _search(ledger, provider, query)
                if not candidates:
                    continue

                random.shuffle(candidates)
                candidates.sort(
                    key=lambda c: (c.duration < needed_seconds + DURATION_MARGIN_S,)
                )

                for candidate in candidates:
                    if downloads >= MAX_DOWNLOADS_PER_RUN:
                        warnings.append("Đã thử tối đa 4 clip trong một lần chạy.")
                        break

                    row_id = ledger.reserve(
                        candidate.provider, candidate.video_id,
                        file_url=candidate.file_url, query=query, run_id=run_id,
                    )
                    if row_id is None:
                        continue  # another run holds it

                    downloads += 1
                    try:
                        clip, rejected = _fetch_and_verify(
                            tools, ledger, candidate, row_id, query,
                            motion_threshold, say,
                        )
                    except Exception as exc:
                        log.warning("ứng viên %s hỏng: %s", candidate.video_id, exc)
                        ledger.release(row_id)
                        continue

                    if clip is not None:
                        return Selection(clip=clip, ledger_row_id=row_id,
                                         warnings=tuple(warnings))
                    if rejected is not None and (
                        best_rejected is None or rejected.motion_score > best_rejected[0]
                    ):
                        best_rejected = (rejected.motion_score, rejected, row_id)
                if downloads >= MAX_DOWNLOADS_PER_RUN:
                    break
            if downloads >= MAX_DOWNLOADS_PER_RUN:
                break
    else:
        warnings.append("Chưa cấu hình PEXELS_API_KEY / PIXABAY_API_KEY.")

    if best_rejected is not None:
        score, clip, row_id = best_rejected
        say(f"Không clip nào đạt ngưỡng chuyển động — dùng clip động nhất ({score:.1f}).")
        warnings.append(
            f"Mọi ứng viên đều dưới ngưỡng {motion_threshold:.1f}; "
            f"đã dùng clip có điểm cao nhất ({score:.1f}). "
            "Cân nhắc hạ ngưỡng trong phần Tuỳ chọn."
        )
        ledger.mark(row_id, "reserved")
        return Selection(clip=clip, ledger_row_id=row_id, warnings=tuple(warnings))

    recycled = _recycle(ledger, say)
    if recycled is not None:
        warnings.append(
            "Kho video nền đã cạn — đang dùng lại một clip cũ (>180 ngày)."
        )
        return Selection(clip=recycled, ledger_row_id=None, recycled=True,
                         warnings=tuple(warnings))

    local = _emergency_pack(tools, say)
    if local is not None:
        warnings.append("Đang dùng clip dự phòng trong assets/backgrounds/.")
        return Selection(clip=local, ledger_row_id=None, recycled=True,
                         warnings=tuple(warnings))

    raise NoBackgroundAvailable(
        "Không tìm được video nền nào dùng được. " + " ".join(warnings)
    )


def _search(ledger: Ledger, provider: Provider, query: str) -> list[StockClip]:
    blocked = ledger.blocked_ids(provider.name)
    try:
        ledger.record_call(provider.name)
        results = provider.search(query, per_page=60, page=random.randint(1, 3),
                                  portrait=True)
    except Exception as exc:
        log.warning("%s search lỗi: %s", provider.name, exc)
        return []

    return [
        clip for clip in results
        if clip.video_id not in blocked
        and not Q.is_calm(clip.page_url, clip.file_url)
        and clip.duration >= 4.0
    ]


def _fetch_and_verify(
    tools: FfmpegTools,
    ledger: Ledger,
    candidate: StockClip,
    row_id: int,
    query: str,
    motion_threshold: float,
    say: Callable[[str], None],
) -> tuple[BackgroundClip | None, BackgroundClip | None]:
    """Return ``(accepted, rejected_but_usable)``.

    The second element lets the caller fall back to the liveliest clip it saw
    when the motion threshold turns out to be set too high for this corpus.
    """
    target = PATHS.cache_backgrounds / f"{candidate.provider}_{candidate.video_id}.mp4"
    say(f"Tải clip {candidate.provider}/{candidate.video_id}…")
    digest = download(candidate.file_url, target)

    if ledger.hash_seen(digest):
        say("Clip này trùng nội dung với một clip đã dùng — bỏ qua.")
        ledger.mark(row_id, "dead_link", content_sha256=digest)
        return None, None

    info = media_info(tools, target)
    score = motion_score(tools, target)
    say(f"Điểm chuyển động: {score:.1f} (ngưỡng {motion_threshold:.1f})")

    clip = _to_clip(candidate, target, info, score, query)

    if score < motion_threshold:
        # Sticky: a calm clip is never downloaded again.
        ledger.mark(row_id, "rejected_low_motion", content_sha256=digest,
                    motion_score=score, duration=info.duration,
                    width=info.width, height=info.height, fps=info.fps)
        return None, clip

    ledger.mark(row_id, "reserved", content_sha256=digest, motion_score=score,
                duration=info.duration, width=info.width, height=info.height,
                fps=info.fps)
    return clip, None


def _to_clip(candidate: StockClip, target: Path, info, score: float,
             query: str) -> BackgroundClip:
    return BackgroundClip(
        provider=candidate.provider, video_id=candidate.video_id,
        file_url=candidate.file_url, path=target,
        duration=info.duration or candidate.duration,
        width=info.width or candidate.width,
        height=info.height or candidate.height,
        fps=info.fps or candidate.fps,
        motion_score=score, query=query,
        author=candidate.author, author_url=candidate.author_url,
    )


def _recycle(ledger: Ledger, say: Callable[[str], None]) -> BackgroundClip | None:
    for row in ledger.recyclable():
        path = PATHS.cache_backgrounds / f"{row['provider']}_{row['video_id']}.mp4"
        if path.is_file():
            say(f"Dùng lại clip cũ {path.name}")
            return BackgroundClip(
                provider=row["provider"], video_id=row["video_id"],
                file_url=row["file_url"] or "", path=path,
                duration=row["duration"] or 0.0, width=row["width"] or 0,
                height=row["height"] or 0, fps=row["fps"] or 0.0,
                motion_score=row["motion_score"] or 0.0, query=row["query"] or "",
            )
    return None


def _emergency_pack(tools: FfmpegTools, say: Callable[[str], None]) -> BackgroundClip | None:
    clips = sorted(PATHS.emergency_backgrounds.glob("*.mp4"))
    if not clips:
        return None
    chosen = random.choice(clips)
    say(f"Dùng clip dự phòng {chosen.name}")
    info = media_info(tools, chosen)
    return BackgroundClip(
        provider="local", video_id=chosen.stem, file_url="", path=chosen,
        duration=info.duration, width=info.width, height=info.height,
        fps=info.fps, motion_score=0.0, query="local",
    )


def confirm(ledger: Ledger, selection: Selection) -> None:
    """Promote the reservation once the render has actually succeeded."""
    if selection.ledger_row_id is not None:
        ledger.mark(selection.ledger_row_id, "confirmed")


def release(ledger: Ledger, selection: Selection | None) -> None:
    """Hand the clip back so a failed run does not permanently consume it."""
    if selection and selection.ledger_row_id is not None:
        ledger.release(selection.ledger_row_id)
