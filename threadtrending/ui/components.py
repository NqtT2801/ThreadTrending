"""Shared Streamlit widgets."""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

import streamlit as st

from ..curate.scoring import CorpusStats, estimate_seconds
from ..curate.structure import cohesion_reasons
from ..errors import ThreadTrendingError
from ..models import Slot
from ..render.timeline import LEAD_IN, SEG_GAP, TAIL

ROLE_LABELS = {
    "hook": "🎣 Hook (gây sốc)",
    "context": "🧭 Bối cảnh",
    "explain": "📖 Giải thích",
    "counter": "↩️ Phản biện",
    "reaction": "💬 Phản ứng",
    "punchline": "🎬 Chốt hạ",
}

ROLE_SHORT = {k: v.split(" ", 1)[1] for k, v in ROLE_LABELS.items()}


def stepper(current: int, labels: dict) -> None:
    columns = st.columns(len(labels))
    for column, (step, label) in zip(columns, labels.items()):
        if step < current:
            column.success(label, icon="✅")
        elif step == current:
            column.info(label, icon="▶️")
        else:
            column.caption(label)


def error_panel(exc: Exception, *, run_dir: Path | None = None) -> None:
    """Show the failure with its hint and the artefacts needed to debug it."""
    hint = getattr(exc, "hint", "")
    st.error(f"**{type(exc).__name__}** — {exc}")
    if hint:
        st.info(hint, icon="💡")

    if run_dir and run_dir.exists():
        with st.expander("Tệp chẩn đoán"):
            for name in ("graph.txt", "timeline.json", "manifest.json",
                         "voice.graph.txt", "debug/page.html"):
                path = run_dir / name
                if path.exists():
                    st.caption(f"`{path}`  ({path.stat().st_size / 1024:.0f} KB)")


def total_duration(slots: Sequence[Slot], rate: float = 1.0) -> float:
    """Narration plus the padding the timeline will insert."""
    included = [s for s in slots if s.include]
    if not included:
        return 0.0
    speech = sum(estimate_seconds(s.tts_text, rate) for s in included)
    return speech + SEG_GAP * (len(included) - 1) + LEAD_IN + TAIL


def duration_meter(slots: Sequence[Slot], budget_s: float, rate: float = 1.0) -> float:
    total = total_duration(slots, rate)
    over = total > budget_s
    columns = st.columns([3, 1, 1])
    columns[0].progress(min(1.0, total / max(1.0, budget_s)))
    columns[1].metric("Thời lượng", f"{total:.0f}s", delta=f"{total - budget_s:+.0f}s",
                      delta_color="inverse" if over else "normal")
    columns[2].metric("Số card", str(sum(1 for s in slots if s.include)))
    if over:
        st.warning(
            f"Vượt ngân sách {budget_s:.0f}s — bỏ bớt vài bình luận "
            "hoặc tăng tốc độ đọc.",
            icon="⏱️",
        )
    return total


def sequence_rationale(slots: Sequence[Slot], stats: CorpusStats) -> None:
    """The "why this order" panel: makes the narrative logic inspectable."""
    included = [s for s in slots if s.include]
    if not included:
        return
    lines = [f"**0. {ROLE_SHORT.get(included[0].role, included[0].role)}** "
             f"— @{included[0].comment.username} · mở màn"]
    for index, (previous, current) in enumerate(zip(included, included[1:]), start=1):
        reasons = cohesion_reasons(previous.comment, current.comment, stats)
        marker = "🔗" if current.replies_to_previous else "·"
        lines.append(
            f"**{index}. {ROLE_SHORT.get(current.role, current.role)}** "
            f"— @{current.comment.username} {marker} {' · '.join(reasons)}"
        )
    st.markdown("\n\n".join(lines))


def attribution(background) -> None:
    if not background or not background.author:
        return
    source = "Pexels" if background.provider == "pexels" else background.provider.title()
    if background.author_url:
        st.caption(f"Video nền: [{background.author}]({background.author_url}) trên {source}")
    else:
        st.caption(f"Video nền: {background.author} trên {source}")


def guard(fn, *args, **kwargs):
    """Run a pipeline call, turning known failures into a readable panel."""
    try:
        return fn(*args, **kwargs), None
    except ThreadTrendingError as exc:
        return None, exc
    except Exception as exc:  # noqa: BLE001 - surfaced to the user verbatim
        return None, exc
