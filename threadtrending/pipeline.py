"""Orchestration: URL in, MP4 out.

Split into two halves because the UI needs to interrupt in the middle:
:func:`prepare` scrapes and curates so the user can review the sequence, and
:func:`render` turns an approved :class:`RenderSpec` into a video.

One subtlety worth stating plainly. A card shows the **original** comment --
emoji, teencode and all, because that is what makes it look like a real Threads
screenshot -- while the voice reads a **normalized** version. When a comment is
long enough to paginate, the split is computed on the display text and each
page is normalized independently; that keeps page turns and speech aligned,
which a split computed on two different strings could not.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from .background.ledger import Ledger
from .background.providers import build_providers
from .background.selector import Selection, confirm, release, select_background
from .cards.renderer import CardRenderer
from .config import Settings
from .curate.assemble import curate_heuristic, retime
from .curate.llm import curate_with_gemini
from .curate.quality import Rejection, filter_comments
from .curate.scoring import CorpusStats
from .curate.tts_text import normalize_for_tts
from .errors import ScrapeEmpty, ThreadTrendingError
from .ffmpeg_locator import FfmpegTools
from .models import (
    BackgroundClip, CardModel, RenderItem, RenderSpec, Slot, Thread, Timeline,
)
from .paths import PATHS, new_run_id
from .render.compose import ComposeRequest, compose
from .render.timeline import build_timeline
from .render.voicebed import build_sfx_bed, build_voice_bed
from .scrape import avatars as avatar_mod
from .scrape import post as post_mod
from .scrape import serp as serp_mod
from .scrape import url as url_mod
from .tts.google_tts import Voice, build_client, synthesize

log = logging.getLogger(__name__)

Progress = Callable[[str, float], None]


def _noop(_message: str, _fraction: float) -> None:
    pass


@dataclass
class Preparation:
    """Everything the review step needs."""

    run_id: str
    target: url_mod.TrendTarget
    thread: Thread
    slots: list[Slot]
    rejections: list[Rejection] = field(default_factory=list)
    stats: CorpusStats = field(default_factory=CorpusStats)
    used_llm: bool = False
    warnings: list[str] = field(default_factory=list)
    permalink: str = ""

    @property
    def trend_name(self) -> str:
        return self.thread.trend_name or self.target.display_name


@dataclass
class RenderResult:
    run_id: str
    video: Path
    timeline: Timeline
    background: BackgroundClip
    voice: Path
    graph: Path
    warnings: list[str] = field(default_factory=list)
    seconds: float = 0.0


# --------------------------------------------------------------------------
# Stage 1: scrape + curate
# --------------------------------------------------------------------------
def prepare(
    browser_manager: Any,
    settings: Settings,
    raw_url: str,
    *,
    run_id: str | None = None,
    budget_s: float | None = None,
    rate: float = 1.0,
    use_llm: bool = True,
    pasted_json: str = "",
    progress: Progress = _noop,
) -> Preparation:
    """Scrape the trend, then build the narrative sequence."""
    run_id = run_id or new_run_id()
    budget_s = budget_s if budget_s is not None else settings.duration_budget_s
    run_dir = PATHS.run_dir(run_id)
    warnings: list[str] = []

    # The pasted-JSON escape hatch has to work with no URL at all -- it exists
    # precisely for when the scraper (and therefore the link) is not usable.
    if pasted_json.strip():
        try:
            target = url_mod.parse(raw_url)
        except url_mod.InvalidUrl:
            target = url_mod.TrendTarget(kind="post", url=raw_url.strip(), query="Threads")
    else:
        target = url_mod.parse(raw_url)

    progress("Phân tích link…", 0.02)

    if pasted_json.strip():
        thread = post_mod.thread_from_json(
            pasted_json, trend_name=target.display_name, source_url=target.url
        )
        permalink = target.url
    else:
        permalink, thread = _scrape(browser_manager, target, run_dir, progress)

    if thread is None or not thread.replies:
        raise ScrapeEmpty(f"Không có bình luận nào ở {permalink}.")

    progress(f"Đã lấy {len(thread.replies)} bình luận. Đang lọc…", 0.45)
    kept, rejections = filter_comments(thread.replies, root_username=thread.root.username)
    if not kept:
        raise ScrapeEmpty(
            f"Tất cả {len(thread.replies)} bình luận đều bị lọc bỏ "
            "(spam / quá ngắn / trùng lặp)."
        )

    stats = CorpusStats.build(kept)
    progress("Đang chọn và xếp thứ tự bình luận…", 0.6)

    used_llm = False
    if use_llm and settings.has_gemini:
        result = curate_with_gemini(
            kept, thread.root.pk, stats,
            api_key=settings.gemini_api_key or "",
            model=settings.gemini_model,
            budget_s=budget_s, rate=rate,
        )
        slots, used_llm = result.slots, result.used_llm
        if not used_llm:
            warnings.append("Gemini không dùng được — đã quay về thuật toán heuristic.")
        warnings.extend(result.errors[:3])
    else:
        if use_llm and not settings.has_gemini:
            warnings.append("Chưa có GEMINI_API_KEY — đang dùng thuật toán heuristic.")
        slots = curate_heuristic(kept, thread.root.pk, stats, budget_s=budget_s, rate=rate)

    if not slots:
        raise ScrapeEmpty("Không xếp được chuỗi bình luận nào từ dữ liệu này.")

    progress(f"Đã chọn {len(slots)} bình luận.", 0.75)
    return Preparation(
        run_id=run_id, target=target, thread=thread, slots=slots,
        rejections=rejections, stats=stats, used_llm=used_llm,
        warnings=warnings, permalink=permalink,
    )


def _scrape(
    browser_manager: Any,
    target: url_mod.TrendTarget,
    run_dir: Path,
    progress: Progress,
) -> tuple[str, Thread | None]:
    """Resolve the target to a permalink and scrape the richest thread available."""
    if target.kind == "post":
        progress("Đang mở bài viết…", 0.1)
        result = post_mod.fetch_thread(
            browser_manager, target.permalink() if target.username else target.url,
            trend_name=target.display_name, debug_dir=run_dir / "debug",
        )
        return target.url, result.thread

    progress("Đang mở trang trend (cần đăng nhập)…", 0.08)
    permalinks = serp_mod.fetch_trend_permalinks(browser_manager, target, limit=5)

    best: tuple[int, str, Thread | None] = (0, permalinks[0], None)
    for index, link in enumerate(permalinks[:3]):
        progress(f"Đang đọc bài {index + 1}/{min(3, len(permalinks))}…", 0.12 + 0.1 * index)
        try:
            result = post_mod.fetch_thread(
                browser_manager, link, trend_name=target.display_name,
                debug_dir=run_dir / "debug" if index == 0 else None,
            )
        except Exception as exc:
            log.warning("bỏ qua %s: %s", link, exc)
            continue
        if result.thread and len(result.thread.replies) > best[0]:
            best = (len(result.thread.replies), link, result.thread)
        # A thread this rich already tells the story; stop paying for more.
        if best[0] >= 40:
            break

    return best[1], best[2]


# --------------------------------------------------------------------------
# Stage 2: synthesize, render, compose
# --------------------------------------------------------------------------
def render(
    browser_manager: Any,
    settings: Settings,
    tools: FfmpegTools,
    prep: Preparation,
    spec: RenderSpec,
    voice: Voice,
    *,
    ledger: Ledger | None = None,
    progress: Progress = _noop,
) -> RenderResult:
    """Turn an approved sequence into the finished MP4."""
    started = time.time()
    ledger = ledger or Ledger()
    run_dir = PATHS.run_dir(spec.run_id)
    warnings = list(prep.warnings)
    selection: Selection | None = None

    ledger.start_run(
        spec.run_id, url=prep.target.url,
        post_code=prep.target.post_code, trend_name=spec.trend_name,
    )

    try:
        progress("Đang tổng hợp giọng đọc và dựng card…", 0.05)
        items = _build_items(
            browser_manager, settings, tools, prep, spec, voice, run_dir, progress
        )
        if not items:
            raise ThreadTrendingError("Không dựng được card nào.")

        progress("Đang dựng timeline…", 0.5)
        timeline = build_timeline(items)

        progress("Đang chọn video nền…", 0.55)
        selection = select_background(
            tools, ledger,
            build_providers(settings.pexels_api_key, settings.pixabay_api_key),
            run_id=spec.run_id, needed_seconds=timeline.total,
            trend_name=spec.trend_name,
            progress=lambda m: progress(m, 0.6),
        )
        warnings.extend(selection.warnings)

        progress("Đang dựng track âm thanh…", 0.72)
        voice_track = build_voice_bed(
            tools, timeline, run_dir / "voice.wav",
            graph_path=run_dir / "voice.graph.txt",
        )
        sfx_track = None
        if spec.sfx_path:
            sfx_track = build_sfx_bed(
                tools, timeline, spec.sfx_path, run_dir / "sfx.wav",
                graph_path=run_dir / "sfx.graph.txt",
            )

        banner = None
        if spec.show_banner:
            banner = run_dir / "banner.png"
            CardRenderer(browser_manager, theme=spec.theme).render_banner(
                spec.trend_name, banner
            )

        progress("Đang ghép video…", 0.78)
        video = compose(
            tools,
            ComposeRequest(
                timeline=timeline, background=selection.clip.path, voice=voice_track,
                out_path=run_dir / "final.mp4", graph_path=run_dir / "graph.txt",
                banner=banner, music=spec.music_path, sfx=sfx_track,
                music_volume=spec.music_volume, sfx_volume=spec.sfx_volume,
                encoder=spec.encoder,
            ),
            progress=lambda f: progress("Đang encode…", 0.78 + 0.2 * f),
        )

        confirm(ledger, selection)
        ledger.finish_run(
            spec.run_id, status="done", out_path=str(video),
            background_id=selection.ledger_row_id,
        )
        _write_manifest(run_dir, prep, spec, timeline, selection.clip, warnings)

        progress("Xong.", 1.0)
        return RenderResult(
            run_id=spec.run_id, video=video, timeline=timeline,
            background=selection.clip, voice=voice_track,
            graph=run_dir / "graph.txt", warnings=warnings,
            seconds=time.time() - started,
        )
    except Exception:
        # A crashed run must not permanently consume the background clip.
        release(ledger, selection)
        ledger.finish_run(spec.run_id, status="failed")
        raise


def _build_items(
    browser_manager: Any,
    settings: Settings,
    tools: FfmpegTools,
    prep: Preparation,
    spec: RenderSpec,
    voice: Voice,
    run_dir: Path,
    progress: Progress,
) -> list[RenderItem]:
    """Per slot: lay out the card, then synthesize one clip per page."""
    client = build_client(settings.google_tts_credentials or {})
    renderer = CardRenderer(browser_manager, theme=spec.theme)
    included = [s for s in spec.slots if s.include]
    items: list[RenderItem] = []

    for index, slot in enumerate(included):
        share = 0.05 + 0.42 * (index / max(1, len(included)))
        progress(f"Bình luận {index + 1}/{len(included)}…", share)

        previous = included[index - 1] if index else None
        reply_to = (
            previous.comment.username
            if spec.show_reply_context and previous
            and slot.comment.parent_pk == previous.comment.pk
            else None
        )

        model = CardModel(
            username=slot.comment.username,
            text=slot.comment.text,
            avatar_data_uri=avatar_mod.fetch_avatar(
                browser_manager, slot.comment.username, slot.comment.profile_pic_url
            ),
            is_verified=slot.comment.is_verified,
            timestamp_label=_relative_time(slot.comment.taken_at),
            like_count=slot.comment.like_count,
            reply_count=slot.comment.reply_count,
            reply_to_username=reply_to,
            theme=spec.theme,
        )

        pages, font_px = renderer.layout(model)
        for page in pages:
            # A single-page card can use the curator's reading of the whole
            # comment; a split one is normalized page by page so the page turn
            # lands exactly where the voice does.
            spoken = slot.tts_text if len(pages) == 1 else normalize_for_tts(page.text)
            if not spoken.strip():
                continue
            audio = synthesize(
                client, spoken, voice, rate=spec.speaking_rate,
                tools=tools, out_dir=PATHS.cache_tts,
            )
            png = renderer.render(model, page, font_px, out_dir=run_dir / "cards")
            items.append(RenderItem(
                pk=slot.comment.pk, page=page.index, of=page.of,
                png=png, audio_path=audio, role=slot.role,
            ))
    return items


def _relative_time(taken_at: int) -> str:
    """Threads-style relative timestamp."""
    if not taken_at:
        return ""
    delta = datetime.now(timezone.utc) - datetime.fromtimestamp(taken_at, tz=timezone.utc)
    seconds = max(0, int(delta.total_seconds()))
    if seconds < 60:
        return f"{seconds} giây"
    if seconds < 3600:
        return f"{seconds // 60} phút"
    if seconds < 86400:
        return f"{seconds // 3600} giờ"
    if seconds < 7 * 86400:
        return f"{seconds // 86400} ngày"
    return f"{seconds // (7 * 86400)} tuần"


def _write_manifest(
    run_dir: Path,
    prep: Preparation,
    spec: RenderSpec,
    timeline: Timeline,
    background: BackgroundClip,
    warnings: Sequence[str],
) -> None:
    import json

    payload = {
        "run_id": spec.run_id,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_url": prep.target.url,
        "permalink": prep.permalink,
        "trend_name": spec.trend_name,
        "used_llm": prep.used_llm,
        "voice": spec.voice_name,
        "speaking_rate": spec.speaking_rate,
        "duration": timeline.total,
        "background": {
            "provider": background.provider, "video_id": background.video_id,
            "motion_score": round(background.motion_score, 2),
            "query": background.query, "author": background.author,
            "author_url": background.author_url,
        },
        "sequence": [
            {
                "pk": s.comment.pk, "role": s.role, "username": s.comment.username,
                "likes": s.comment.like_count, "replies_to_previous": s.replies_to_previous,
                "text": s.comment.text, "tts_text": s.tts_text,
                "est_seconds": round(s.est_seconds, 2), "reason": s.reason,
            }
            for s in spec.slots if s.include
        ],
        "warnings": list(warnings),
    }
    (run_dir / "manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (run_dir / "timeline.json").write_text(
        json.dumps(
            [
                {
                    "idx": s.idx, "pk": s.comment_pk, "page": s.page,
                    "start": s.start, "audio_at": s.audio_at, "end": s.end,
                    "audio_dur": s.audio_dur, "card": s.card_png.name,
                }
                for s in timeline.segments
            ],
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
