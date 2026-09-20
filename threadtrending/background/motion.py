"""The motion gate: how "energetic" is this clip, really?

Mean absolute frame-to-frame luma difference over a six-second sample at 1/8
scale. Cheap (one pass, tiny frames) and it maps onto the thing we actually
care about. Measured empirically:

* locked-off sunset / slow ocean / candle: 1-3
* moderate camera movement:                4-6
* traffic, dance, racing, particles:       8-25

A clip below :data:`MOTION_THRESHOLD` is recorded as ``rejected_low_motion`` and
never downloaded again.
"""
from __future__ import annotations

import logging
import re
import statistics
from pathlib import Path

from ..ffmpeg_locator import FfmpegTools, run_quiet

log = logging.getLogger(__name__)

#: Calibrated against synthetic clips (static gradient 0.0, slow drift 0.3,
#: colour-bar pan 2.1, noise storm 15.6). Real stock footage has not been swept,
#: so the selector treats this as a preference rather than a hard gate: if every
#: candidate is rejected it takes the liveliest one instead of failing. Raise it
#: once you have seen real scores in the ledger.
MOTION_THRESHOLD = 4.5
SAMPLE_START = 2.0
SAMPLE_DURATION = 6.0

YAVG_RE = re.compile(r"lavfi\.signalstats\.YAVG=([\d.]+)")


def motion_score(tools: FfmpegTools, path: Path) -> float:
    """Mean |frame delta| luma over the sample window. Higher means busier."""
    proc = run_quiet([
        str(tools.ffmpeg), "-hide_banner", "-v", "error",
        "-ss", str(SAMPLE_START), "-t", str(SAMPLE_DURATION),
        "-i", str(path),
        # file=- is required: metadata=print logs at AV_LOG_INFO, which
        # "-v error" suppresses entirely. Writing to stdout instead makes the
        # measurement independent of the log level.
        "-vf", "scale=160:-2,tblend=all_mode=difference,signalstats,"
               "metadata=print:key=lavfi.signalstats.YAVG:file=-",
        "-f", "null", "-",
    ], timeout=120)

    values = [float(v) for v in YAVG_RE.findall(proc.stdout + proc.stderr)]
    if not values:
        log.debug("no YAVG samples for %s; treating as unknown", path.name)
        return 0.0
    return statistics.mean(values)


def is_energetic(score: float, threshold: float = MOTION_THRESHOLD) -> bool:
    return score >= threshold
