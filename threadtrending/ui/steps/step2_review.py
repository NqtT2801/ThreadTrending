"""Step 2 -- review, reorder and edit the narrative sequence.

The most important screen in the app: the whole premise is that the comments
tell a story, and this is where that claim is checked by a human. The "vì sao
thứ tự này" panel exists so the ordering is inspectable rather than magical.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from ...config import Settings
from ...curate.assemble import curate_heuristic
from ...curate.llm import curate_with_gemini
from ...curate.scoring import estimate_seconds
from ...models import ROLES, Slot
from .. import state as S
from ..components import (
    ROLE_LABELS, ROLE_SHORT, duration_meter, error_panel, guard, sequence_rationale,
)


def render(settings: Settings) -> None:
    prep = st.session_state.prep
    if prep is None:
        st.info("Chưa có dữ liệu. Quay lại bước 1.")
        return

    st.subheader(f"Mạch truyện · {prep.trend_name}")
    _header(prep)

    for warning in prep.warnings:
        st.warning(warning, icon="⚠️")

    frame = _to_frame(prep.slots)
    edited = st.data_editor(
        frame,
        use_container_width=True,
        hide_index=True,
        num_rows="fixed",
        height=min(620, 90 + 46 * len(frame)),
        column_config={
            "include": st.column_config.CheckboxColumn("Dùng", width="small"),
            "order": st.column_config.NumberColumn(
                "#", width="small", min_value=1, max_value=len(frame), step=1,
                help="Sửa số rồi bấm 'Áp dụng thứ tự'.",
            ),
            "role": st.column_config.SelectboxColumn(
                "Vai trò", options=[ROLE_LABELS[r] for r in ROLES], width="medium"
            ),
            "username": st.column_config.TextColumn("Tác giả", disabled=True, width="small"),
            "text": st.column_config.TextColumn("Nội dung (sửa được)", width="large"),
            "likes": st.column_config.NumberColumn("♥", disabled=True, width="small"),
            "depth": st.column_config.NumberColumn("Sâu", disabled=True, width="small"),
            "reply_to": st.column_config.TextColumn("↳ trả lời", disabled=True, width="small"),
            "seconds": st.column_config.NumberColumn(
                "giây", disabled=True, format="%.1f", width="small"
            ),
        },
        key="sequence_editor",
    )

    slots = _apply_edits(prep.slots, edited)
    prep.slots = slots

    duration_meter(slots, settings.duration_budget_s)
    _changed_notice(prep.slots, edited)

    with st.expander("Vì sao thứ tự này?", expanded=True):
        sequence_rationale(slots, prep.stats)

    with st.expander(f"Đã lọc bỏ {len(prep.rejections)} bình luận"):
        if prep.rejections:
            st.dataframe(
                pd.DataFrame([
                    {"lý do": r.label, "tác giả": r.comment.username,
                     "nội dung": r.comment.text[:110]}
                    for r in prep.rejections
                ]),
                use_container_width=True, hide_index=True, height=240,
            )

    _actions(settings, prep)


def _header(prep) -> None:
    columns = st.columns(4)
    columns[0].metric("Bình luận lấy được", len(prep.thread.replies))
    columns[1].metric("Đưa vào video", sum(1 for s in prep.slots if s.include))
    columns[2].metric(
        "Cặp đối đáp thật", sum(1 for s in prep.slots if s.replies_to_previous)
    )
    columns[3].metric("Chọn bởi", "Gemini" if prep.used_llm else "Heuristic")


def _to_frame(slots: list[Slot]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "include": s.include,
            "order": i + 1,
            "role": ROLE_LABELS.get(s.role, s.role),
            "username": s.comment.username,
            "text": s.comment.text,
            "likes": s.comment.like_count,
            "depth": s.comment.depth,
            "reply_to": "✔" if s.replies_to_previous else "",
            "seconds": round(s.est_seconds, 1),
        }
        for i, s in enumerate(slots)
    ])


def _apply_edits(slots: list[Slot], frame: pd.DataFrame) -> list[Slot]:
    """Fold the table back into Slots, re-deriving anything text changes affect."""
    label_to_role = {v: k for k, v in ROLE_LABELS.items()}
    updated: list[tuple[int, Slot]] = []

    for position, slot in enumerate(slots):
        row = frame.iloc[position]
        text = str(row["text"]).strip() or slot.comment.text
        changed = text != slot.comment.text

        slot.include = bool(row["include"])
        slot.role = label_to_role.get(str(row["role"]), slot.role)
        if changed:
            # Editing the text changes the TTS cache key, so the estimate and the
            # spoken line must both follow it.
            slot.tts_text = text
            slot.est_seconds = estimate_seconds(text, 1.0)
        updated.append((int(row["order"]), slot))

    updated.sort(key=lambda pair: pair[0])
    ordered = [slot for _, slot in updated]

    for index, slot in enumerate(ordered):
        previous = ordered[index - 1] if index else None
        slot.replies_to_previous = bool(
            previous and slot.comment.parent_pk == previous.comment.pk
        )
    return ordered


def _changed_notice(slots: list[Slot], frame: pd.DataFrame) -> None:
    changed = sum(
        1 for i, s in enumerate(slots)
        if str(frame.iloc[i]["text"]).strip() != s.comment.text
    )
    if changed:
        st.info(
            f"{changed} bình luận đã sửa → sẽ tổng hợp lại giọng đọc (~{changed * 1.5:.0f}s).",
            icon="🔁",
        )


def _actions(settings: Settings, prep) -> None:
    columns = st.columns([1, 1, 1, 2])

    if columns[0].button("Xếp lại (heuristic)", use_container_width=True):
        kept = [s.comment for s in prep.slots] or list(prep.thread.replies)
        prep.slots = curate_heuristic(
            list(prep.thread.replies), prep.thread.root.pk, prep.stats,
            budget_s=settings.duration_budget_s,
        )
        prep.used_llm = False
        st.rerun()

    if columns[1].button(
        "Xếp lại (Gemini)", use_container_width=True,
        disabled=not settings.has_gemini,
        help="" if settings.has_gemini else "Cần GEMINI_API_KEY trong .env",
    ):
        with st.spinner("Gemini đang xếp lại…"):
            result, error = guard(
                curate_with_gemini,
                list(prep.thread.replies), prep.thread.root.pk, prep.stats,
                api_key=settings.gemini_api_key or "",
                model=settings.gemini_model,
                budget_s=settings.duration_budget_s,
            )
        if error is not None:
            error_panel(error)
        else:
            prep.slots = result.slots
            prep.used_llm = result.used_llm
            prep.warnings = result.errors[:3]
            st.rerun()

    if columns[2].button("← Quay lại", use_container_width=True):
        S.goto(S.Step.INPUT)

    if columns[3].button(
        "Tiếp tục → Tuỳ chọn", type="primary", use_container_width=True,
        disabled=not any(s.include for s in prep.slots),
    ):
        S.goto(S.Step.OPTIONS)
