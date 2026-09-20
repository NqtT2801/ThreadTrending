"""The single source of truth for timing.

Every visibility window, fade, SFX offset and the output ``-t`` is derived from
the *measured* duration of the synthesized audio. Nothing here estimates. The
estimator in :mod:`..curate.scoring` exists only to decide how many comments to
pick before any audio has been made.

Padding vocabulary:

* ``LEAD_IN``   -- silence before the first card appears
* ``PRE_ROLL``  -- card is on screen this long before its voice starts
* ``POST_ROLL`` -- card lingers this long after its voice ends
* ``SEG_GAP``   -- pause between two different comments
* ``PAGE_GAP``  -- pause between pages of the *same* comment (much shorter, so
  the two pages read as one card whose text advances)
* ``TAIL``      -- silence after the last card disappears
"""
from __future__ import annotations

import logging
from typing import Sequence

from ..models import RenderItem, Segment, Timeline
from .probe import wav_duration

log = logging.getLogger(__name__)

LEAD_IN = 0.60
PRE_ROLL = 0.18
POST_ROLL = 0.35
SEG_GAP = 0.30
PAGE_GAP = 0.12
TAIL = 1.20

FADE_IN = 0.22
FADE_OUT = 0.22
#: Vertical slide distance for a card's entrance, in pixels.
SLIDE_PX = 24

TIMING_TOLERANCE = 0.05


def build_timeline(items: Sequence[RenderItem]) -> Timeline:
    """Lay the rendered pages out on an absolute timeline."""
    if not items:
        return Timeline(segments=(), total=0.0)

    segments: list[Segment] = []
    cursor = LEAD_IN

    for index, item in enumerate(items):
        duration = wav_duration(item.audio_path)
        start = cursor
        audio_at = start + PRE_ROLL
        end = audio_at + duration + POST_ROLL

        segments.append(
            Segment(
                idx=index,
                comment_pk=item.pk,
                page=item.page,
                card_png=item.png,
                audio_path=item.audio_path,
                audio_dur=duration,
                start=round(start, 4),
                audio_at=round(audio_at, 4),
                end=round(end, 4),
                # Continuation pages neither fade nor slide: the card should look
                # like it is turning a page, not like a new card arrived.
                fade_in=0.0 if item.page > 0 else FADE_IN,
                fade_out=FADE_OUT,
            )
        )

        is_last = index == len(items) - 1
        next_is_same_comment = (not is_last) and items[index + 1].page > 0
        cursor = end + (0.0 if is_last else (PAGE_GAP if next_is_same_comment else SEG_GAP))

    total = round(cursor + TAIL, 3)
    _assert_sane(segments, total)
    log.info("timeline: %d segments, %.2fs total", len(segments), total)
    return Timeline(segments=tuple(segments), total=total)


def _assert_sane(segments: Sequence[Segment], total: float) -> None:
    """Overlapping cards would mean two visible at once; that must never happen."""
    for previous, current in zip(segments, segments[1:]):
        if current.start < previous.end - 1e-6:
            raise AssertionError(
                f"segments {previous.idx} and {current.idx} overlap: "
                f"{previous.end:.3f} > {current.start:.3f}"
            )
    if segments and segments[-1].end > total + 1e-6:
        raise AssertionError("last segment extends past the declared total")


def sfx_offsets(timeline: Timeline) -> list[float]:
    """One transition sound per comment -- not per page."""
    return [s.start for s in timeline.segments if s.page == 0]


def describe(timeline: Timeline) -> str:
    lines = [f"total {timeline.total:.2f}s over {len(timeline.segments)} segments"]
    for s in timeline.segments:
        lines.append(
            f"  #{s.idx:02d} pk={s.comment_pk:>10} p{s.page} "
            f"card {s.start:6.2f}-{s.end:6.2f}s  voice @{s.audio_at:6.2f}s "
            f"({s.audio_dur:4.2f}s)"
        )
    return "\n".join(lines)
