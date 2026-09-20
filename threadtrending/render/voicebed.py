"""Pass A: lay the per-segment clips onto silence at their exact offsets.

Kept separate from the video pass for three reasons: it finishes in under a
second, it is what the UI's "nghe thử" player uses, and it keeps the video
filtergraph purely visual so a failure there is unambiguously a video problem.

Offsets are computed in **samples** and only then converted to milliseconds, so
rounding error can never exceed one sample no matter how many segments there are.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Sequence

from ..errors import FfmpegError
from ..ffmpeg_locator import FfmpegTools, run_quiet
from ..models import Timeline
from ..tts.constants import SAMPLE_RATE
from .filtergraph import voice_bed_graph, write_graph
from .probe import wav_duration
from .timeline import TIMING_TOLERANCE, sfx_offsets

log = logging.getLogger(__name__)

#: ffmpeg's filtergraph gets unwieldy past this; the pipeline caps at 12 anyway.
MAX_MIX_INPUTS = 60


def offset_ms(seconds: float) -> int:
    """Seconds -> milliseconds, via samples, so drift cannot accumulate."""
    return round(round(seconds * SAMPLE_RATE) / SAMPLE_RATE * 1000)


def build_voice_bed(
    tools: FfmpegTools,
    timeline: Timeline,
    out_path: Path,
    *,
    graph_path: Path | None = None,
) -> Path:
    """Mix every segment's speech onto a silent bed of ``timeline.total``."""
    clips = [(offset_ms(s.audio_at), s.audio_path) for s in timeline.segments]
    return _mix(tools, clips, timeline.total, out_path, graph_path, label="voice")


def build_sfx_bed(
    tools: FfmpegTools,
    timeline: Timeline,
    sfx_path: Path,
    out_path: Path,
    *,
    graph_path: Path | None = None,
) -> Path | None:
    """One transition sound at the start of every comment (not every page)."""
    offsets = sfx_offsets(timeline)
    if not offsets or not sfx_path.is_file():
        return None
    clips = [(offset_ms(t), sfx_path) for t in offsets]
    return _mix(tools, clips, timeline.total, out_path, graph_path, label="sfx")


def _mix(
    tools: FfmpegTools,
    clips: Sequence[tuple[int, Path]],
    total: float,
    out_path: Path,
    graph_path: Path | None,
    *,
    label: str,
) -> Path:
    if not clips:
        raise FfmpegError(f"Không có clip nào để dựng track {label}.")
    if len(clips) > MAX_MIX_INPUTS:
        raise FfmpegError(f"Quá nhiều input cho amix ({len(clips)}).")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    graph = voice_bed_graph([ms for ms, _ in clips], len(clips))
    graph_file = graph_path or out_path.with_suffix(".graph.txt")
    write_graph(graph, graph_file)

    argv = [
        str(tools.ffmpeg), "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-t", f"{total:.3f}",
        "-i", f"anullsrc=r={SAMPLE_RATE}:cl=mono",
    ]
    for _, path in clips:
        argv += ["-i", str(path)]
    argv += tools.filter_script_args(graph_file)
    argv += [
        "-map", "[vo]",
        "-c:a", "pcm_s16le", "-ar", str(SAMPLE_RATE), "-ac", "1",
        str(out_path),
    ]

    proc = run_quiet(argv, timeout=180)
    if proc.returncode != 0 or not out_path.exists():
        raise FfmpegError(
            f"Dựng track {label} thất bại: {proc.stderr.strip()[-400:]}",
            hint=f"Xem filtergraph tại {graph_file}",
        )

    produced = wav_duration(out_path)
    drift = abs(produced - total)
    if drift > TIMING_TOLERANCE:
        raise FfmpegError(
            f"Track {label} dài {produced:.3f}s nhưng timeline là {total:.3f}s "
            f"(lệch {drift:.3f}s > {TIMING_TOLERANCE}s)"
        )
    log.info("%s bed: %d clips, %.3fs (lệch %.3fs)", label, len(clips), produced, drift)
    return out_path
