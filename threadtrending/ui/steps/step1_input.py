"""Step 1 -- paste a link, check the session, scrape and curate."""
from __future__ import annotations

import streamlit as st

from ...config import Settings
from ...paths import PATHS
from ...pipeline import prepare
from ...scrape import auth
from ...scrape.url import InvalidUrl, parse
from .. import state as S
from ..components import error_panel, guard

EXAMPLE = (
    "https://www.threads.com/search?q=Tuy%E1%BB%83n%20th%E1%BB%A7%20PUBG"
    "&serp_type=trends&trend_fbid=1072142712480212"
)


def render(settings: Settings) -> None:
    st.subheader("Nhập link Threads")

    url = st.text_input(
        "Link trend hoặc link bài viết",
        value=st.session_state.url,
        placeholder=EXAMPLE,
        help="Trang trend (/search?q=…&serp_type=trends) cần đăng nhập. "
             "Permalink bài viết (/@user/post/…) thì không.",
    )
    st.session_state.url = url

    target = None
    if url.strip():
        try:
            target = parse(url)
        except InvalidUrl as exc:
            st.error(str(exc))

    if target:
        columns = st.columns(3)
        columns[0].metric("Loại", "Trang trend" if target.kind == "trend" else "Bài viết")
        columns[1].metric("Chủ đề", target.display_name or "—")
        columns[2].metric("Cần đăng nhập", "Có" if target.needs_login else "Không")

    _session_panel(target)
    _preflight_panel(settings)

    with st.expander("Dán JSON thủ công (khi scraper hỏng)"):
        st.caption(
            "Mở bài viết trên Threads, xem source, copy nội dung một thẻ "
            "`<script type=\"application/json\" data-sjs>` có chứa `thread_items` "
            "rồi dán vào đây. Bước này bỏ qua hoàn toàn trình duyệt."
        )
        st.session_state.pasted_json = st.text_area(
            "JSON", value=st.session_state.pasted_json, height=120,
            label_visibility="collapsed",
        )

    use_llm = st.toggle(
        "Dùng Gemini để chọn và xếp thứ tự bình luận",
        value=settings.has_gemini,
        disabled=not settings.has_gemini,
        help=("Cần GEMINI_API_KEY trong .env. Không có thì tool tự dùng thuật toán "
              "heuristic."),
    )

    disabled = target is None and not st.session_state.pasted_json.strip()
    if st.button("Lấy bình luận", type="primary", disabled=disabled,
                 use_container_width=True):
        _run_prepare(settings, use_llm)


def _session_panel(target) -> None:
    if target is not None and not target.needs_login:
        return

    left, right = st.columns([3, 1])
    login_state = st.session_state.login_state

    with left:
        if login_state is None:
            st.caption("Chưa kiểm tra phiên đăng nhập Threads.")
        elif login_state.signed_in:
            st.success(login_state.badge, icon="🔓")
        else:
            st.warning(f"{login_state.badge} — {login_state.detail}", icon="🔒")

    with right:
        if st.button("Kiểm tra phiên", use_container_width=True):
            with st.spinner("Đang kiểm tra…"):
                st.session_state.login_state = auth.probe(S.browser_manager())
            st.rerun()

    if login_state is not None and not login_state.signed_in:
        st.info(
            "Bấm nút dưới để mở cửa sổ trình duyệt, đăng nhập Threads một lần. "
            "Phiên được lưu lại trong `data/playwright-profile/` nên chỉ cần làm "
            "một lần trên mỗi máy.",
            icon="💡",
        )
        if st.button("Mở browser để đăng nhập"):
            with st.spinner("Đang chờ bạn đăng nhập (tối đa 4 phút)…"):
                st.session_state.login_state = auth.open_login_window(S.browser_manager())
            st.rerun()


def _preflight_panel(settings: Settings) -> None:
    pre = S.preflight(settings)
    if pre.error:
        st.error(f"Không tìm thấy ffmpeg: {pre.error}")
        st.code("winget install --id=Gyan.FFmpeg -e --source winget", language="powershell")
        if st.button("Kiểm tra lại"):
            st.cache_data.clear()
            st.rerun()
        return

    with st.expander(f"Môi trường · ffmpeg {pre.tools.version}"):
        for warning in pre.warnings:
            st.warning(warning, icon="⚠️")
        rows = {
            "Google Cloud TTS": settings.has_tts,
            "Gemini (chọn bình luận)": settings.has_gemini,
            "Pexels": bool(settings.pexels_api_key),
            "Pixabay": bool(settings.pixabay_api_key),
            "h264_qsv (Intel)": pre.tools.supports("h264_qsv"),
        }
        for name, ok in rows.items():
            st.caption(f"{'✅' if ok else '⬜'} {name}")
        if not settings.has_tts:
            st.warning(
                "Chưa có service account Google Cloud TTS — không render được giọng đọc.",
                icon="⚠️",
            )
        if st.button("Nạp lại cấu hình"):
            S.reload_settings()
            st.rerun()


def _run_prepare(settings: Settings, use_llm: bool) -> None:
    run_id = S.new_run()
    status = st.status("Đang lấy dữ liệu…", expanded=True)

    def on_progress(message: str, fraction: float) -> None:
        status.update(label=message)
        status.write(f"{fraction:.0%} · {message}")

    prep, error = guard(
        prepare,
        S.browser_manager(), settings, st.session_state.url,
        run_id=run_id,
        budget_s=settings.duration_budget_s,
        use_llm=use_llm,
        pasted_json=st.session_state.pasted_json,
        progress=on_progress,
    )

    if error is not None:
        status.update(label="Thất bại", state="error")
        error_panel(error, run_dir=PATHS.output / run_id)
        return

    status.update(label=f"Đã chọn {len(prep.slots)} bình luận", state="complete")
    st.session_state.prep = prep
    S.goto(S.Step.REVIEW)
