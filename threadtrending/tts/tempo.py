"""Pitch-preserving speed changes for voices that cannot do it themselves.

``atempo`` only accepts 0.5..2.0 per instance, so anything outside that range is
expressed as a chain. The file is rewritten in place because its cache key
already encodes the rate.
"""
from __future__ import annotations

import logging
from pathlib import Path

from ..errors import FfmpegError
from ..ffmpeg_locator import FfmpegTools, run_quiet

log = logging.getLogger(__name__)

ATEMPO_MIN, ATEMPO_MAX = 0.5, 2.0


def atempo_chain(rate: float) -> list[str]:
    """Factor ``rate`` into atempo stages each within the legal range."""
    if abs(rate - 1.0) < 0.01:
        return []
    remaining = max(0.25, min(4.0, rate))
    stages: list[float] = []
    while remaining > ATEMPO_MAX:
        stages.append(ATEMPO_MAX)
        remaining /= ATEMPO_MAX
    while remaining < ATEMPO_MIN:
        stages.append(ATEMPO_MIN)
        remaining /= ATEMPO_MIN
    stages.append(remaining)
    return [f"atempo={s:.6f}".rstrip("0").rstrip(".") for s in stages]


def apply_tempo(tools: FfmpegTools, wav_path: Path, rate: float) -> Path:
    """Rewrite ``wav_path`` at the requested speed. No-op for rate == 1.0."""
    chain = atempo_chain(rate)
    if not chain:
        return wav_path

    temp = wav_path.with_suffix(".tempo.wav")
    proc = run_quiet([
        str(tools.ffmpeg), "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(wav_path),
        "-filter:a", ",".join(chain),
        "-c:a", "pcm_s16le", "-ar", "24000", "-ac", "1",
        str(temp),
    ], timeout=120)

    if proc.returncode != 0 or not temp.exists():
        temp.unlink(missing_ok=True)
        raise FfmpegError(f"atempo thất bại: {proc.stderr.strip()[-300:]}")

    temp.replace(wav_path)
    return wav_path
