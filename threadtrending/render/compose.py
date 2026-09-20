"""Pass B: one ffmpeg invocation that produces the finished MP4.

Composition strategy: a single ``-filter_complex`` holding N timed overlays.
At most one card is visible at any instant (the windows never intersect), so the
per-frame cost is one 864x384 alpha blend regardless of N, and there are no
intermediate files to re-encode. Pre-rendering an alpha track would need this
same graph to build it and then pay a full RGBA encode/decode on top;
per-segment concat would re-encode every piece and break background continuity
at the seams.

The command is always built as an argv list with ``shell=False``. That is not
tidiness -- PowerShell mangles the commas, semicolons, brackets and quotes that
fill an ffmpeg command line, so the shell must never see one. The filtergraph
itself goes in a file (see :meth:`FfmpegTools.filter_script_args`), which also
sidesteps the 32767-character Windows command-line limit.
"""
from __future__ import annotations

import logging
import re
import subprocess
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from ..errors import FfmpegError
from ..ffmpeg_locator import FfmpegTools, _CREATE_NO_WINDOW
from ..models import Timeline
from .filtergraph import FPS, VideoInputs, video_graph, write_graph

log = logging.getLogger(__name__)

PROGRESS_RE = re.compile(r"out_time_us=(-?\d+)")
STDERR_TAIL = 40

X264_PARAMS = "keyint=60:min-keyint=60:scenecut=0"
QSV_FAILURE_MARKERS = ("qsv", "device creation failed", "hwaccel", "impl_idx")


@dataclass
class ComposeRequest:
    timeline: Timeline
    background: Path
    voice: Path
    out_path: Path
    graph_path: Path
    banner: Path | None = None
    music: Path | None = None
    sfx: Path | None = None
    music_volume: float = 0.13
    sfx_volume: float = 0.35
    encoder: str = "libx264"
    crf: int = 20
    preset: str = "medium"


def build_argv(tools: FfmpegTools, request: ComposeRequest) -> tuple[list[str], VideoInputs]:
    """Assemble the argv and the input-index map the filtergraph refers to."""
    argv: list[str] = [
        str(tools.ffmpeg), "-y", "-hide_banner", "-loglevel", "error",
        "-nostats", "-progress", "pipe:1",
    ]
    index = 0

    # Loop the background: selector prefers clips longer than the video, but a
    # short one must still fill the duration. -t on the output ends it.
    argv += ["-stream_loop", "-1", "-i", str(request.background)]
    background = index
    index += 1

    argv += ["-i", str(request.voice)]
    voice = index
    index += 1

    sfx_idx = None
    if request.sfx and request.sfx.is_file():
        argv += ["-i", str(request.sfx)]
        sfx_idx = index
        index += 1

    music_idx = None
    if request.music and request.music.is_file():
        argv += ["-stream_loop", "-1", "-i", str(request.music)]
        music_idx = index
        index += 1

    first_card = index
    for segment in request.timeline.segments:
        argv += [
            "-loop", "1", "-framerate", str(FPS),
            "-t", f"{segment.duration:.3f}",
            "-i", str(segment.card_png),
        ]
        index += 1

    banner_idx = None
    if request.banner and request.banner.is_file():
        argv += ["-loop", "1", "-framerate", str(FPS),
                 "-t", f"{request.timeline.total:.3f}", "-i", str(request.banner)]
        banner_idx = index
        index += 1

    inputs = VideoInputs(
        background=background, voice=voice, sfx=sfx_idx, music=music_idx,
        first_card=first_card, banner=banner_idx,
        n_cards=len(request.timeline.segments),
    )

    argv += tools.filter_script_args(request.graph_path)
    argv += [
        "-map", "[v]", "-map", "[a]",
        "-t", f"{request.timeline.total:.3f}",
    ]
    argv += _encoder_args(request)
    argv += [
        # 48 kHz stereo is not optional: some short-form ingests silently
        # resample or reject mono / 24 kHz audio.
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
        "-movflags", "+faststart",
        str(request.out_path),
    ]
    return argv, inputs


def _encoder_args(request: ComposeRequest) -> list[str]:
    if request.encoder == "h264_qsv":
        return [
            "-c:v", "h264_qsv", "-global_quality", str(request.crf + 2),
            "-look_ahead", "1", "-preset", "veryfast", "-pix_fmt", "nv12",
        ]
    return [
        # High@4.0 + yuv420p is the profile every platform accepts; yuv444 gets
        # rejected or badly transcoded. keyint=60 gives clean 2-second GOPs.
        "-c:v", "libx264", "-preset", request.preset, "-crf", str(request.crf),
        "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.0",
        "-x264-params", X264_PARAMS,
    ]


def compose(
    tools: FfmpegTools,
    request: ComposeRequest,
    *,
    progress: Callable[[float], None] | None = None,
    allow_qsv_fallback: bool = True,
) -> Path:
    """Render the video, reporting progress in 0..1. Falls back off QSV silently."""
    request.out_path.parent.mkdir(parents=True, exist_ok=True)

    argv, inputs = build_argv(tools, request)
    graph = video_graph(
        request.timeline, inputs,
        music_volume=request.music_volume,
        sfx_volume=request.sfx_volume,
    )
    write_graph(graph, request.graph_path)

    log.info(
        "compose: %d cards, %.2fs, encoder=%s -> %s",
        len(request.timeline.segments), request.timeline.total,
        request.encoder, request.out_path.name,
    )

    try:
        _run(argv, request.timeline.total, progress)
    except FfmpegError as exc:
        lowered = str(exc).lower()
        if (
            allow_qsv_fallback
            and request.encoder == "h264_qsv"
            and any(marker in lowered for marker in QSV_FAILURE_MARKERS)
        ):
            log.warning("QSV không khởi tạo được, chuyển sang libx264")
            request.encoder = "libx264"
            return compose(tools, request, progress=progress, allow_qsv_fallback=False)
        raise

    if not request.out_path.exists() or request.out_path.stat().st_size < 1024:
        raise FfmpegError("ffmpeg kết thúc nhưng không tạo ra file video hợp lệ.")
    return request.out_path


def _run(argv: Sequence[str], total: float, progress: Callable[[float], None] | None) -> None:
    """Stream ``-progress`` from stdout while keeping the last stderr lines."""
    proc = subprocess.Popen(
        list(argv),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        shell=False,
        creationflags=_CREATE_NO_WINDOW,
    )

    assert proc.stdout is not None
    total_us = max(1.0, total * 1_000_000)
    try:
        for line in proc.stdout:
            if progress and (match := PROGRESS_RE.search(line)):
                progress(max(0.0, min(1.0, int(match.group(1)) / total_us)))
    finally:
        proc.stdout.close()
        stderr = proc.stderr.read() if proc.stderr else ""
        if proc.stderr:
            proc.stderr.close()
        returncode = proc.wait()

    if returncode != 0:
        tail = deque(stderr.strip().splitlines(), maxlen=STDERR_TAIL)
        raise FfmpegError(
            "ffmpeg lỗi (exit {}):\n{}".format(returncode, "\n".join(tail))
        )
    if progress:
        progress(1.0)
