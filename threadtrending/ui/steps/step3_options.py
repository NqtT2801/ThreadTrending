"""Step 3 -- voice, look and audio options."""
from __future__ import annotations

import random
from pathlib import Path

import streamlit as st

from ...config import Settings
from ...curate.assemble import retime
from ...models import RenderSpec
from ...paths import PATHS
from ...tts.google_tts import Voice, build_client, default_voice, list_voices
from .. import state as S
from ..components import duration_meter, error_panel, guard


def render(settings: Settings) -> None:
    prep = st.session_state.prep
    if prep is None:
        st.info("Chưa có dữ liệu. Quay lại bước 1.")
        return

    st.subheader("Tuỳ chọn render")

    voices = _load_voices(settings)
    left, right = st.columns(2)

    with left:
        st.markdown("**Giọng đọc**")
        voice = _voice_picker(voices)
        rate = st.slider(
            "Tốc độ đọc", 0.8, 1.6, 1.15, 0.05,
            help="Video ngắn thường đọc nhanh hơn bình thường một chút.",
        )
        if voice and not voice.supports_speaking_rate:
            st.caption(
                f"Giọng {voice.family} không nhận `speaking_rate` — tốc độ sẽ "
                "được áp bằng ffmpeg `atempo` (giữ nguyên cao độ)."
            )

        st.markdown("**Hình ảnh**")
        theme = st.radio(
            "Tông card", ["dark", "light"], horizontal=True,
            format_func=lambda v: "Tối" if v == "dark" else "Sáng",
        )
        show_banner = st.toggle(
            "Hiện banner tên trend", value=True,
            help="Dải chữ nhỏ ở trên, nằm trong margin 10%, hiện suốt video.",
        )
        show_reply_context = st.toggle(
            "Hiện dải '↳ trả lời @…'", value=True,
            help="Cho người xem thấy đây là một cuộc đối đáp, không phải slideshow.",
        )

    with right:
        st.markdown("**Âm thanh**")
        music = _pick_asset(
            PATHS.music, "Nhạc nền",
            "Thả file .mp3/.m4a vào `assets/music/`. Nhạc sẽ tự ducking khi có giọng đọc.",
        )
        music_volume = st.slider("Âm lượng nhạc nền", 0.0, 0.4, 0.13, 0.01,
                                 disabled=music is None)
        sfx = _pick_asset(
            PATHS.sfx, "Tiếng chuyển card",
            "Thả file pop/whoosh ngắn vào `assets/sfx/`.",
        )
        sfx_volume = st.slider("Âm lượng SFX", 0.0, 0.8, 0.35, 0.05, disabled=sfx is None)

        st.markdown("**Kỹ thuật**")
        preflight = S.preflight(settings)
        qsv = preflight.ok and preflight.tools.supports("h264_qsv")
        encoder = st.radio(
            "Bộ mã hoá", ["libx264", "h264_qsv"],
            format_func=lambda v: "libx264 (CPU, ổn định)" if v == "libx264"
            else "h264_qsv (Intel, nhanh hơn)",
            disabled=not qsv,
            help="QSV nhanh hơn ~2-3x trên UHD 770; nếu khởi tạo lỗi tool tự quay về libx264.",
        )
        budget = st.slider(
            "Ngân sách thời lượng (giây)", 20.0, 90.0,
            float(settings.duration_budget_s), 5.0,
        )

    slots = retime(prep.slots, rate)
    for original, updated in zip(prep.slots, slots):
        original.est_seconds = updated.est_seconds
    st.divider()
    duration_meter(prep.slots, budget, rate)

    if not settings.has_tts:
        st.error(
            "Chưa cấu hình Google Cloud TTS. Đặt `GOOGLE_APPLICATION_CREDENTIALS` "
            "trong `.env`, hoặc thêm bảng `[gcp_service_account]` vào "
            "`.streamlit/secrets.toml`.",
            icon="🔑",
        )

    columns = st.columns([1, 3])
    if columns[0].button("← Quay lại", use_container_width=True):
        S.goto(S.Step.REVIEW)

    if columns[1].button(
        "Render video", type="primary", use_container_width=True,
        disabled=not (settings.has_tts and voice and preflight.ok),
    ):
        st.session_state.spec = RenderSpec(
            run_id=st.session_state.run_id,
            trend_name=prep.trend_name,
            source_url=prep.target.url,
            slots=prep.slots,
            voice_name=voice.name if voice else "",
            speaking_rate=rate,
            theme=theme,
            show_reply_context=show_reply_context,
            show_banner=show_banner,
            music_path=music,
            sfx_path=sfx,
            music_volume=music_volume,
            sfx_volume=sfx_volume,
            encoder=encoder,
            duration_budget_s=budget,
        )
        st.session_state.voice_obj = voice
        S.goto(S.Step.RENDER)


def _load_voices(settings: Settings) -> list[Voice]:
    if st.session_state.voices is not None:
        return st.session_state.voices
    if not settings.has_tts:
        return []
    result, error = guard(
        lambda: list_voices(build_client(settings.google_tts_credentials or {}))
    )
    if error is not None:
        error_panel(error)
        st.session_state.voices = []
    else:
        st.session_state.voices = result
    return st.session_state.voices or []


def _voice_picker(voices: list[Voice]) -> Voice | None:
    if not voices:
        st.caption("Chưa lấy được danh sách giọng vi-VN.")
        return None

    families = sorted({v.family for v in voices},
                      key=lambda f: [v.family for v in voices].index(f))
    family = st.selectbox("Nhóm giọng", families, index=0)
    pool = [v for v in voices if v.family == family]
    fallback = default_voice(pool) or pool[0]
    return st.selectbox(
        "Giọng", pool, index=pool.index(fallback),
        format_func=lambda v: v.label,
    )


def _pick_asset(directory: Path, label: str, help_text: str) -> Path | None:
    files = sorted(p for p in directory.glob("*") if p.suffix.lower() in
                   {".mp3", ".m4a", ".wav", ".aac", ".ogg"})
    if not files:
        st.caption(f"{label}: chưa có file. {help_text}")
        return None

    options = ["(ngẫu nhiên)", "(không dùng)"] + [f.name for f in files]
    choice = st.selectbox(label, options, index=0, help=help_text)
    if choice == "(không dùng)":
        return None
    if choice == "(ngẫu nhiên)":
        return random.choice(files)
    return directory / choice
