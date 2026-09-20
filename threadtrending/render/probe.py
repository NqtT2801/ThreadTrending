"""Media metadata without requiring ffprobe.

Audio durations come from the WAV header via the stdlib ``wave`` module. That is
not a convenience -- it is a correctness requirement. MP3 carries encoder delay
and padding, so every segment would start ~26 ms late and the error compounds
until the voice visibly trails the cards by segment ten. WAV has no such
priming, and ``frames / framerate`` is exact to the sample.

Video metadata is read from ffprobe when present and parsed out of ``ffmpeg -i``
stderr otherwise, so an imageio-ffmpeg install (ffmpeg only, no ffprobe) still
works end to end.
"""
from __future__ import annotations

import contextlib
import json
import logging
import re
import wave
from dataclasses import dataclass
from pathlib import Path

from ..ffmpeg_locator import FfmpegTools, run_quiet

log = logging.getLogger(__name__)

DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d\d):(\d\d(?:\.\d+)?)")
VIDEO_STREAM_RE = re.compile(
    r"Stream #\d+:\d+.*?:\s*Video:.*?(?<!\d)(\d{2,5})x(\d{2,5})(?!\d)", re.S
)
FPS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*fps")


@dataclass(frozen=True)
class MediaInfo:
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0

    @property
    def is_portrait(self) -> bool:
        return self.height > self.width > 0

    @property
    def aspect(self) -> float:
        return self.width / self.height if self.height else 0.0


def wav_duration(path: Path | str) -> float:
    """Exact duration of a PCM WAV file, in seconds."""
    with contextlib.closing(wave.open(str(path), "rb")) as handle:
        frames, rate = handle.getnframes(), handle.getframerate()
    return frames / rate if rate else 0.0


def wav_frames(path: Path | str) -> tuple[int, int]:
    """``(frames, sample_rate)`` -- used when offsets must be sample-exact."""
    with contextlib.closing(wave.open(str(path), "rb")) as handle:
        return handle.getnframes(), handle.getframerate()


def media_info(tools: FfmpegTools, path: Path | str) -> MediaInfo:
    """Duration / dimensions / fps, preferring ffprobe when it is installed."""
    if tools.has_ffprobe:
        info = _via_ffprobe(tools, path)
        if info.duration or info.width:
            return info
    return _via_ffmpeg_stderr(tools, path)


def _via_ffprobe(tools: FfmpegTools, path: Path | str) -> MediaInfo:
    assert tools.ffprobe is not None
    proc = run_quiet([
        str(tools.ffprobe), "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,avg_frame_rate:format=duration",
        "-of", "json", str(path),
    ])
    if proc.returncode != 0:
        return MediaInfo()
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return MediaInfo()

    stream = (data.get("streams") or [{}])[0]
    duration = float((data.get("format") or {}).get("duration") or 0.0)
    return MediaInfo(
        duration=duration,
        width=int(stream.get("width") or 0),
        height=int(stream.get("height") or 0),
        fps=_parse_rational(stream.get("avg_frame_rate")),
    )


def _parse_rational(value: str | None) -> float:
    if not value or "/" not in str(value):
        try:
            return float(value or 0.0)
        except ValueError:
            return 0.0
    numerator, _, denominator = str(value).partition("/")
    try:
        den = float(denominator)
        return float(numerator) / den if den else 0.0
    except ValueError:
        return 0.0


def _via_ffmpeg_stderr(tools: FfmpegTools, path: Path | str) -> MediaInfo:
    """``ffmpeg -i <file>`` with no output: it prints the stream table and exits 1."""
    proc = run_quiet([str(tools.ffmpeg), "-hide_banner", "-i", str(path)])
    text = proc.stderr or ""

    duration = 0.0
    if m := DURATION_RE.search(text):
        hours, minutes, seconds = int(m.group(1)), int(m.group(2)), float(m.group(3))
        duration = hours * 3600 + minutes * 60 + seconds

    width = height = 0
    if m := VIDEO_STREAM_RE.search(text):
        width, height = int(m.group(1)), int(m.group(2))

    fps = 0.0
    if m := FPS_RE.search(text):
        fps = float(m.group(1))

    return MediaInfo(duration=duration, width=width, height=height, fps=fps)
