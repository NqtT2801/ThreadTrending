"""End-to-end render smoke test using synthetic audio and background.

Proves the whole ffmpeg chain -- timeline -> voice bed -> sfx bed -> compose --
without needing Google credentials or the network. Run with:

    .venv/Scripts/python.exe tests/render_video_smoke.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from threadtrending import ffmpeg_locator as fl
from threadtrending.cards import theme as T
from threadtrending.cards.fallback_avatar import initials_avatar
from threadtrending.cards.renderer import CardRenderer
from threadtrending.ffmpeg_locator import run_quiet
from threadtrending.models import CardModel, RenderItem
from threadtrending.render.compose import ComposeRequest, compose
from threadtrending.render.probe import media_info, wav_duration
from threadtrending.render.timeline import build_timeline, describe
from threadtrending.render.voicebed import build_sfx_bed, build_voice_bed
from threadtrending.scrape.browser import BrowserManager

OUT = Path(__file__).resolve().parent.parent / "output" / "_render_smoke"

COMMENTS = [
    ("minh.ng", "Ủa cái gì?! Giải nghệ giữa giải là sao trời ơi 😱", None, 3100, 42, 2.6),
    ("hoanganh_", "Chuyện là team bị tố dàn xếp tỉ số từ vòng bảng, ban tổ chức "
     "đang điều tra nên bạn ấy rút lui.", "minh.ng", 2400, 18, 4.4),
    ("minh.ng", "Chưa chắc đâu, nghe một phía thôi. Chưa có bằng chứng gì cả.",
     "hoanganh_", 1800, 9, 3.6),
    ("khanhlinh92", "Đầu đuôi là hôm qua có người tung đoạn tin nhắn ra ngoài.",
     None, 2900, 31, 3.4),
    ("duc.tran", "Ngồi hóng tiếp 🍿", "khanhlinh92", 900, 2, 1.2),
    ("quangminh", "Thôi xong, drama này còn dài.", None, 700, 4, 1.8),
]


def synth_wav(tools, path: Path, seconds: float, freq: int) -> Path:
    """A stand-in for a TTS clip: a tone envelope of an exact known length."""
    path.parent.mkdir(parents=True, exist_ok=True)
    proc = run_quiet([
        str(tools.ffmpeg), "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate=24000:duration={seconds}",
        "-af", f"afade=t=in:d=0.05,afade=t=out:st={max(0, seconds - 0.12):.2f}:d=0.12,volume=0.5",
        "-c:a", "pcm_s16le", "-ar", "24000", "-ac", "1", str(path),
    ], timeout=60)
    if proc.returncode != 0:
        raise SystemExit(f"synth failed: {proc.stderr[-300:]}")
    return path


def synth_background(tools, path: Path, seconds: float) -> Path:
    """High-motion synthetic clip -- stands in for an energetic stock video."""
    proc = run_quiet([
        str(tools.ffmpeg), "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"testsrc2=size=1080x1920:rate=30:duration={seconds}",
        "-vf", "hue=H=2*PI*t/6",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p",
        str(path),
    ], timeout=180)
    if proc.returncode != 0:
        raise SystemExit(f"background synth failed: {proc.stderr[-300:]}")
    return path


def synth_music(tools, path: Path, seconds: float) -> Path:
    proc = run_quiet([
        str(tools.ffmpeg), "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"sine=frequency=110:sample_rate=48000:duration={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency=165:sample_rate=48000:duration={seconds}",
        "-filter_complex", "[0:a][1:a]amix=inputs=2:normalize=0,volume=0.4[a]",
        "-map", "[a]", "-c:a", "aac", "-b:a", "128k", str(path),
    ], timeout=60)
    if proc.returncode != 0:
        raise SystemExit(f"music synth failed: {proc.stderr[-300:]}")
    return path


def synth_sfx(tools, path: Path) -> Path:
    """Short 'pop' used on every card change."""
    proc = run_quiet([
        str(tools.ffmpeg), "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "sine=frequency=900:sample_rate=24000:duration=0.14",
        "-af", "afade=t=out:st=0.03:d=0.11,volume=0.6",
        "-c:a", "pcm_s16le", "-ar", "24000", "-ac", "1", str(path),
    ], timeout=60)
    if proc.returncode != 0:
        raise SystemExit(f"sfx synth failed: {proc.stderr[-300:]}")
    return path


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    tools = fl.resolve()
    print(f"ffmpeg {tools.version}")

    started = time.time()

    # --- 1. cards ---------------------------------------------------------
    items: list[RenderItem] = []
    with BrowserManager() as bm:
        renderer = CardRenderer(bm, theme="dark", cache_dir=OUT / "cards")
        for i, (user, text, reply_to, likes, replies, seconds) in enumerate(COMMENTS):
            model = CardModel(
                username=user, text=text, avatar_data_uri=initials_avatar(user),
                is_verified=(i == 0), timestamp_label=f"{i + 1} giờ",
                like_count=likes, reply_count=replies, reply_to_username=reply_to,
            )
            fitted = renderer.render_all(model, out_dir=OUT / "cards")
            per_page = seconds / len(fitted)
            for card in fitted:
                wav = synth_wav(
                    tools, OUT / "seg" / f"{len(items):03d}.wav",
                    per_page, 220 + 40 * (len(items) % 6),
                )
                items.append(RenderItem(
                    pk=f"pk{i}", page=card.page.index, of=card.page.of,
                    png=card.png, audio_path=wav, role="explain",
                ))
        banner = renderer.render_banner("Tuyển thủ PUBG", OUT / "banner.png")
    print(f"cards: {len(items)} pages rendered")

    # --- 2. timeline ------------------------------------------------------
    timeline = build_timeline(items)
    print(describe(timeline))

    # --- 3. audio beds ----------------------------------------------------
    background = synth_background(tools, OUT / "bg.mp4", 12.0)  # deliberately short
    music = synth_music(tools, OUT / "music.m4a", 20.0)         # also short: must loop
    sfx_src = synth_sfx(tools, OUT / "pop.wav")

    voice = build_voice_bed(tools, timeline, OUT / "voice.wav",
                            graph_path=OUT / "voice.graph.txt")
    sfx = build_sfx_bed(tools, timeline, sfx_src, OUT / "sfx.wav",
                        graph_path=OUT / "sfx.graph.txt")
    print(f"voice bed: {wav_duration(voice):.3f}s (timeline {timeline.total:.3f}s)")
    print(f"sfx bed  : {wav_duration(sfx):.3f}s, {len(timeline.sfx_offsets())} hits")

    # --- 4. compose -------------------------------------------------------
    last = [0.0]

    def on_progress(fraction: float) -> None:
        if fraction - last[0] >= 0.25 or fraction >= 1.0:
            last[0] = fraction
            print(f"  encoding {fraction:.0%}")

    final = compose(tools, ComposeRequest(
        timeline=timeline, background=background, voice=voice,
        out_path=OUT / "final.mp4", graph_path=OUT / "graph.txt",
        banner=banner, music=music, sfx=sfx,
    ), progress=on_progress)

    # --- 5. verify --------------------------------------------------------
    info = media_info(tools, final)
    size_mb = final.stat().st_size / 1e6
    print(f"\noutput: {final}  ({size_mb:.1f} MB) in {time.time() - started:.1f}s")
    print(f"  {info.width}x{info.height} @ {info.fps:.0f}fps, {info.duration:.2f}s")

    checks = {
        "1080x1920": (info.width, info.height) == (T.CANVAS_W, T.CANVAS_H),
        "30 fps": abs(info.fps - 30) < 0.5,
        "duration matches timeline": abs(info.duration - timeline.total) < 0.2,
        "non-trivial size": size_mb > 0.1,
    }
    if tools.has_ffprobe:
        streams = run_quiet([
            str(tools.ffprobe), "-v", "error", "-show_entries",
            "stream=codec_name,pix_fmt,sample_rate,channels", "-of", "csv=p=0", str(final),
        ]).stdout
        print(f"  streams: {streams.strip().splitlines()}")
        checks["h264 + yuv420p"] = "h264" in streams and "yuv420p" in streams
        checks["aac 48k stereo"] = "aac" in streams and "48000" in streams and ",2" in streams

    print()
    for name, ok in checks.items():
        print(f"  {'OK  ' if ok else 'FAIL'} {name}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
