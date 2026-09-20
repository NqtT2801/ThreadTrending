"""Step 4 -- run the render and show the result."""
from __future__ import annotations

import streamlit as st

from ...config import Settings
from ...paths import PATHS
from ...pipeline import render as run_pipeline
from .. import state as S
from ..components import attribution, error_panel, guard


def render(settings: Settings) -> None:
    prep = st.session_state.prep
    spec = st.session_state.spec
    if prep is None or spec is None:
        st.info("Chưa có gì để render. Quay lại bước 1.")
        return

    result = st.session_state.result
    if result is None:
        _run(settings, prep, spec)
        return

    _show(result)


def _run(settings: Settings, prep, spec) -> None:
    st.subheader("Đang render")
    status = st.status("Bắt đầu…", expanded=True)
    bar = st.progress(0.0)
    log_area = st.empty()
    lines: list[str] = []

    def on_progress(message: str, fraction: float) -> None:
        bar.progress(min(1.0, max(0.0, fraction)))
        status.update(label=message)
        if not lines or lines[-1] != message:
            lines.append(message)
            log_area.code("\n".join(lines[-12:]), language=None)

    preflight = S.preflight(settings)
    result, error = guard(
        run_pipeline,
        S.browser_manager(), settings, preflight.tools, prep, spec,
        st.session_state.voice_obj,
        ledger=S.ledger(),
        progress=on_progress,
    )

    if error is not None:
        status.update(label="Thất bại", state="error")
        error_panel(error, run_dir=PATHS.output / spec.run_id)
        if st.button("← Sửa tuỳ chọn rồi thử lại"):
            S.goto(S.Step.OPTIONS)
        return

    status.update(label=f"Xong trong {result.seconds:.0f} giây", state="complete")
    st.session_state.result = result
    st.rerun()


def _show(result) -> None:
    st.subheader("Video đã xong")

    columns = st.columns(4)
    columns[0].metric("Thời lượng", f"{result.timeline.total:.1f}s")
    columns[1].metric("Số card", str(len(result.timeline.segments)))
    columns[2].metric("Dung lượng", f"{result.video.stat().st_size / 1e6:.1f} MB")
    columns[3].metric("Render trong", f"{result.seconds:.0f}s")

    for warning in result.warnings:
        st.warning(warning, icon="⚠️")

    st.video(str(result.video))
    attribution(result.background)
    st.caption(
        f"Nền: {result.background.provider}/{result.background.video_id} · "
        f"chuyển động {result.background.motion_score:.1f} · "
        f"truy vấn “{result.background.query}”"
    )

    st.download_button(
        "Tải video",
        data=result.video.read_bytes(),
        file_name=f"{result.run_id}.mp4",
        mime="video/mp4",
        type="primary",
        use_container_width=True,
    )

    with st.expander("Chi tiết timeline"):
        st.dataframe(
            [
                {
                    "#": s.idx, "card": f"{s.start:.2f}s → {s.end:.2f}s",
                    "giọng đọc": f"{s.audio_at:.2f}s ({s.audio_dur:.2f}s)",
                    "trang": f"{s.page + 1}",
                }
                for s in result.timeline.segments
            ],
            use_container_width=True, hide_index=True,
        )
        st.caption(f"Thư mục run: `{PATHS.output / result.run_id}`")

    left, right = st.columns(2)
    if left.button("Render lại với tuỳ chọn khác", use_container_width=True):
        st.session_state.result = None
        S.goto(S.Step.OPTIONS)
    if right.button("Làm video mới", type="primary", use_container_width=True):
        S.reset_run()
        S.goto(S.Step.INPUT)
