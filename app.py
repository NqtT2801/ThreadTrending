"""ThreadTrending -- Threads trending topic to a short-form vertical video.

Run with:  streamlit run app.py
"""
from __future__ import annotations

import streamlit as st

from threadtrending.cards import theme as T
from threadtrending.ui import state as S
from threadtrending.ui.components import stepper
from threadtrending.ui.steps import (
    step1_input, step2_review, step3_options, step4_render,
)

st.set_page_config(
    page_title="ThreadTrending",
    page_icon="🔥",
    layout="wide",
    initial_sidebar_state="expanded",
)

S.init()
settings = S.settings()

st.title("🔥 ThreadTrending")
st.caption(
    "Link trend Threads → video dọc 1080×1920 kể lại vụ việc bằng chính "
    "bình luận của mọi người."
)

with st.sidebar:
    st.header("Trạng thái")
    preflight = S.preflight(settings)
    st.write(f"**ffmpeg** · {preflight.tools.version if preflight.ok else '❌ chưa có'}")
    for name, ok in (
        ("Google TTS", settings.has_tts),
        ("Gemini", settings.has_gemini),
        ("Pexels", bool(settings.pexels_api_key)),
        ("Pixabay", bool(settings.pixabay_api_key)),
    ):
        st.write(f"{'✅' if ok else '⬜'} {name}")

    st.divider()
    st.header("Khung hình")
    st.caption(
        f"Canvas **{T.CANVAS_W}×{T.CANVAS_H}**  \n"
        f"Card rộng **{T.CARD_W}px** ({T.CARD_W / T.CANVAS_W:.0%})  \n"
        f"Cao tối đa **{T.CARD_MAX_H}px** (1/5)  \n"
        f"Lề trên **{T.CARD_Y}px** (10%), căn giữa ngang"
    )

    st.divider()
    ledger = S.ledger()
    st.header("Kho video nền")
    st.metric("Clip đã dùng", ledger.confirmed_count())
    st.metric("Số lần chạy", ledger.run_count())

    st.divider()
    if st.button("Bắt đầu lại", use_container_width=True):
        S.reset_run()
        S.goto(S.Step.INPUT)

stepper(st.session_state.step, S.STEP_LABELS)
st.divider()

match st.session_state.step:
    case S.Step.INPUT:
        step1_input.render(settings)
    case S.Step.REVIEW:
        step2_review.render(settings)
    case S.Step.OPTIONS:
        step3_options.render(settings)
    case S.Step.RENDER:
        step4_render.render(settings)
