"""Generate the ffmpeg filtergraphs, as text written to a file.

Three things in here are load-bearing and easy to get wrong:

**1. Cards must be real looped streams, and are shifted with ``tpad``.**
A PNG opened without ``-loop 1`` is a single frame at PTS 0, so
``fade=t=out:st=3.1`` can never fire -- the filter only ever sees t=0. Each card
is therefore ``-loop 1 -framerate 30 -t <duration>``, with its fades expressed in
its own 0..D timeline, and is then shifted to absolute time. ``tpad`` is used for
the shift rather than ``setpts=PTS+S/TB`` because it *prepends real transparent
frames* from t=0, so overlay's framesync has a frame available at every instant
instead of having to guess. With ``repeatlast=0`` (do not freeze the last frame
forever) and ``eof_action=pass`` (do not end the output when one card's stream
ends) the result is deterministic across ffmpeg 5 through 9.

**2. ``amix`` needs ``normalize=0`` and ``dropout_transition=0``.**
Without the first it divides by the input count and the voice comes out at 1/N.
Without the second it applies a two-second gain ramp whenever an input ends.

**3. Quoting inside the script file is ffmpeg's, not the shell's.**
``enable='between(t,0.6,3.5)'`` still needs its single quotes, because ffmpeg's
own option tokenizer splits on ``:`` and ``,`` without tracking parentheses. The
graph is written to a file and passed via ``-filter_complex_script`` so the
shell never sees it -- and the file is written UTF-8 *without* a BOM, since
ffmpeg does not skip one and fails with an opaque parse error if present.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..cards import theme as T
from ..models import Timeline
from .timeline import SLIDE_PX

FPS = 30
#: Slight darkening + saturation lift so white card text stays readable and the
#: background still looks lively.
BACKGROUND_GRADE = "eq=brightness=-0.05:saturation=1.06"

LOUDNORM = "loudnorm=I=-14:TP=-1.0:LRA=11"


@dataclass(frozen=True)
class VideoInputs:
    """Input indices, in the exact order they are passed on the command line."""

    background: int
    voice: int
    sfx: int | None
    music: int | None
    first_card: int
    banner: int | None
    n_cards: int


def voice_bed_graph(offsets_ms: Sequence[int], n_inputs: int) -> str:
    """Graph for pass A: shift each clip to its offset and mix onto silence.

    Input 0 is an ``anullsrc`` bed of the full duration, which both fixes the
    output length and gives ``amix`` something to align against.
    """
    if not offsets_ms:
        return "[0:a]anull[vo]"

    lines = [
        f"[{i + 1}:a]adelay={ms}:all=1[s{i}];"
        for i, ms in enumerate(offsets_ms[:n_inputs])
    ]
    labels = "".join(f"[s{i}]" for i in range(len(offsets_ms[:n_inputs])))
    lines.append(
        f"[0:a]{labels}amix=inputs={len(offsets_ms[:n_inputs]) + 1}"
        ":duration=longest:normalize=0:dropout_transition=0[vo]"
    )
    return "\n".join(lines)


def _card_chain(input_index: int, label: str, start: float, duration: float,
                fade_in: float, fade_out: float) -> str:
    """Local 0..D timeline for one card, then shifted to absolute ``start``."""
    parts = ["format=rgba"]
    if fade_in > 0:
        parts.append(f"fade=t=in:st=0:d={fade_in:.3f}:alpha=1")
    fade_out_start = max(0.0, duration - fade_out)
    parts.append(f"fade=t=out:st={fade_out_start:.3f}:d={fade_out:.3f}:alpha=1")
    parts.append(
        f"tpad=start_duration={start:.3f}:start_mode=add:color=black@0"
    )
    return f"[{input_index}:v]{','.join(parts)}[{label}];"


def _overlay_y(start: float, fade_in: float) -> str:
    """Constant y, or a short slide-up while the card fades in."""
    if fade_in <= 0:
        return str(T.CARD_Y)
    # Quoted because ffmpeg's tokenizer splits on ',' and ':' inside the expression.
    return (
        f"'{T.CARD_Y}+{SLIDE_PX}*(1-max(0,min(1,(t-{start:.3f})/{fade_in:.3f})))'"
    )


def video_graph(
    timeline: Timeline,
    inputs: VideoInputs,
    *,
    music_volume: float = 0.13,
    sfx_volume: float = 0.35,
    keep_background_audio: bool = False,
) -> str:
    """The full pass-B graph: background, timed card overlays, banner, audio mix."""
    lines: list[str] = []

    lines.append(
        f"[{inputs.background}:v]"
        f"scale={T.CANVAS_W}:{T.CANVAS_H}:force_original_aspect_ratio=increase,"
        f"crop={T.CANVAS_W}:{T.CANVAS_H},setsar=1,fps={FPS},"
        f"{BACKGROUND_GRADE},format=rgba[bg];"
    )

    for offset, segment in enumerate(timeline.segments):
        lines.append(
            _card_chain(
                input_index=inputs.first_card + offset,
                label=f"c{offset}",
                start=segment.start,
                duration=segment.duration,
                fade_in=segment.fade_in,
                fade_out=segment.fade_out,
            )
        )

    current = "bg"
    for offset, segment in enumerate(timeline.segments):
        nxt = f"o{offset}"
        lines.append(
            f"[{current}][c{offset}]overlay="
            f"x={T.CARD_X}:y={_overlay_y(segment.start, segment.fade_in)}:"
            f"enable='between(t,{segment.start:.3f},{segment.end:.3f})':"
            f"eof_action=pass:repeatlast=0[{nxt}];"
        )
        current = nxt

    if inputs.banner is not None:
        # repeatlast=1, unlike the cards: the banner stays for the whole video.
        lines.append(
            f"[{current}][{inputs.banner}:v]overlay="
            f"x={T.BANNER_X}:y={T.BANNER_Y}:eof_action=pass:repeatlast=1[ob];"
        )
        current = "ob"

    lines.append(f"[{current}]format=yuv420p[v];")
    lines.append(_audio_graph(inputs, music_volume, sfx_volume, keep_background_audio))
    return "\n".join(lines)


def _audio_graph(
    inputs: VideoInputs,
    music_volume: float,
    sfx_volume: float,
    keep_background_audio: bool,
) -> str:
    """Voice, ducked music, transition SFX -- then one loudness normalisation.

    Background clip audio is muted by simply never referencing ``0:a``; no ``-an``
    and no ``volume=0`` are required.
    """
    stereo = "aformat=sample_fmts=fltp:channel_layouts=stereo"
    lines = [f"[{inputs.voice}:a]aresample=48000:resampler=soxr,{stereo}[voice];"]

    mix_labels: list[str] = []

    if inputs.music is not None:
        lines.append(
            f"[{inputs.music}:a]aresample=48000,{stereo},volume={music_volume:.3f}[musraw];"
        )
        lines.append("[voice]asplit=2[voice_mix][voice_sc];")
        # Duck the bed against the voice so narration never competes with music.
        lines.append(
            "[musraw][voice_sc]sidechaincompress="
            "threshold=0.05:ratio=8:attack=20:release=400[mus];"
        )
        mix_labels.append("[mus]")
        mix_labels.append("[voice_mix]")
    else:
        mix_labels.append("[voice]")

    if inputs.sfx is not None:
        lines.append(
            f"[{inputs.sfx}:a]aresample=48000,{stereo},volume={sfx_volume:.3f}[sfx];"
        )
        mix_labels.append("[sfx]")

    if keep_background_audio:
        lines.append(f"[{inputs.background}:a]aresample=48000,{stereo},volume=0.07[bga];")
        mix_labels.append("[bga]")

    if len(mix_labels) == 1:
        lines.append(f"{mix_labels[0]}{LOUDNORM}[a]")
    else:
        lines.append(
            f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}"
            ":normalize=0:dropout_transition=0[am];"
        )
        lines.append(f"[am]{LOUDNORM}[a]")
    return "\n".join(lines)


def write_graph(text: str, path) -> None:
    """UTF-8 **without** a BOM -- ffmpeg does not skip one and will fail to parse."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
