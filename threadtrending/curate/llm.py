"""Gemini Flash picks and orders the final sequence.

The model never sees raw scraped data. It receives the top candidates *after*
the quality filter and the heuristic scorers have run, together with the real
reply chains and the heuristic's own proposal as a baseline. That keeps the
request around 4k tokens in / 1.5k out, and -- more importantly -- means the
fallback path is the same code that produced the baseline.

Everything the prompt asks for is re-checked in :func:`validate_plan`. The model
is a ranker here, not a source of truth: it may only select and order ``pk``
values it was given, and every ``tts_text`` it returns is verified against the
original comment before it is allowed anywhere near the voice track.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Sequence

from ..models import Chain, Comment, Role, Slot
from . import structure as st
from .assemble import Candidate, curate_heuristic
from .scoring import CorpusStats, estimate_seconds
from .tts_text import normalize_for_tts

log = logging.getLogger(__name__)

MAX_CANDIDATES = 40
#: Below this token-set F1 against the original, a tts_text is not a reading of
#: that comment any more -- it is a rewrite.
MIN_TTS_FIDELITY = 0.55
#: A token this rare that appears in tts_text but not in the source is invention.
INVENTION_IDF = 6.0
DURATION_SLACK = 1.15

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["sequence", "total_est_seconds"],
    "properties": {
        "sequence": {
            "type": "array",
            "minItems": 5,
            "maxItems": 12,
            "items": {
                "type": "object",
                "required": ["pk", "role", "tts_text", "est_seconds"],
                "properties": {
                    "pk": {"type": "string"},
                    "role": {
                        "type": "string",
                        "enum": ["hook", "context", "explain", "counter",
                                 "reaction", "punchline"],
                    },
                    "tts_text": {"type": "string"},
                    "est_seconds": {"type": "number"},
                    "reason": {"type": "string"},
                },
            },
        },
        "total_est_seconds": {"type": "number"},
        "dropped_pks": {"type": "array", "items": {"type": "string"}},
    },
}

SYSTEM_PROMPT = """\
Bạn là biên tập viên dựng video ngắn từ bình luận Threads tiếng Việt.

NHIỆM VỤ: từ danh sách CANDIDATES, CHỌN và SẮP XẾP 8-12 bình luận thành một mạch
truyện mà người xem hiểu được vụ việc chỉ qua chính các bình luận đó.

QUY TẮC BẮT BUỘC:
1. Chỉ được dùng giá trị "pk" có trong CANDIDATES. Một pk lạ làm hỏng toàn bộ kết quả.
2. KHÔNG viết bình luận mới, KHÔNG gộp hai bình luận, KHÔNG dịch, KHÔNG tóm tắt,
   KHÔNG làm mềm, KHÔNG đổi lập trường hay ý nghĩa. Bạn chọn và xếp, bạn không sáng tác.
3. "tts_text" là bản đọc thành tiếng của chính "txt" đó: bung viết tắt/teencode, đọc số
   thành chữ, bỏ emoji/hashtag/URL/@tên, gộp dấu câu lặp. GIỮ NGUYÊN trật tự từ, văn
   phong slang và thái độ của người viết. Không cần sửa gì thì chép nguyên "txt".
4. Phần tử đầu tiên bắt buộc role="hook": bình luận GÂY SỐC hoặc khơi tò mò mạnh nhất.
   Phần tử cuối bắt buộc role="punchline". Mỗi loại đúng một cái.
5. Các bình luận sau hook phải GIẢI THÍCH cho hook, không được bẻ sang chủ đề khác.
6. Ưu tiên các cặp liên tiếp mà "p" của cái sau bằng "pk" của cái trước (reply thật),
   hoặc hai cái cùng nằm trong một entry của "chains". MỘT REPLY THẬT CÓ GIÁ TRỊ HƠN
   HAI BÌNH LUẬN CHỈ GIỐNG CHỦ ĐỀ.
7. Tổng "est_seconds" không được vượt "budget_s". Vượt thì BỎ BỚT phần tử và liệt kê
   vào "dropped_pks" — tuyệt đối không cắt xén nghĩa của một bình luận cho vừa.

GIẢI NGHĨA TRƯỜNG: pk=id, u=tác giả, d=độ sâu, p=pk của bình luận cha, t=thời điểm,
lk=lượt thích, rp=lượt trả lời, txt=nội dung gốc, sh/ex/co=điểm sốc/giải thích/phản biện.

Chỉ xuất JSON đúng schema. Không kèm văn xuôi, không kèm markdown."""


@dataclass
class LlmResult:
    slots: list[Slot]
    used_llm: bool
    errors: list[str]
    raw: dict[str, Any] | None = None


def build_payload(
    candidates: Sequence[Candidate],
    chains: Sequence[Chain],
    baseline: Sequence[Slot],
    budget_s: float,
) -> str:
    """Compact JSONL-ish payload. Short keys keep the request small."""
    lines = []
    for cand in candidates[:MAX_CANDIDATES]:
        c, f = cand.comment, cand.feats
        lines.append(
            json.dumps(
                {
                    "pk": c.pk, "u": c.username, "d": c.depth,
                    "p": c.parent_pk or "", "t": c.taken_at,
                    "lk": c.like_count, "rp": c.reply_count,
                    "txt": c.text,
                    "sh": round(f.shock, 2), "ex": round(f.explain, 2),
                    "co": round(f.counter, 2),
                },
                ensure_ascii=False,
            )
        )

    context = {
        "budget_s": round(budget_s, 1),
        "chains": [list(ch.pks) for ch in chains[:8]],
        "baseline_order": [s.comment.pk for s in baseline],
    }
    return (
        "CANDIDATES:\n" + "\n".join(lines)
        + "\n\nCONTEXT:\n" + json.dumps(context, ensure_ascii=False)
        + "\n\nbaseline_order là đề xuất của thuật toán heuristic. "
          "Hãy cải thiện nó nếu bạn tìm được mạch truyện tốt hơn."
    )


def validate_plan(
    raw: dict[str, Any],
    candidates: Sequence[Candidate],
    stats: CorpusStats,
    budget_s: float,
    rate: float = 1.0,
) -> tuple[list[Slot], list[str]]:
    """Turn the model's JSON into Slots, or explain exactly why it is unusable."""
    from . import normalize as nz

    errors: list[str] = []
    by_pk = {c.pk: c.comment for c in candidates}
    sequence = raw.get("sequence")
    if not isinstance(sequence, list) or not sequence:
        return [], ["sequence rỗng hoặc sai kiểu"]

    slots: list[Slot] = []
    seen: set[str] = set()

    for i, item in enumerate(sequence):
        if not isinstance(item, dict):
            errors.append(f"phần tử {i} không phải object")
            continue
        pk = str(item.get("pk", ""))
        comment = by_pk.get(pk)
        if comment is None:
            errors.append(f"pk '{pk}' không có trong CANDIDATES")
            continue
        if pk in seen:
            errors.append(f"pk '{pk}' bị lặp")
            continue
        seen.add(pk)

        role = item.get("role")
        if role not in ("hook", "context", "explain", "counter", "reaction", "punchline"):
            errors.append(f"pk '{pk}' có role không hợp lệ: {role!r}")
            role = "explain"

        tts_text, why = _validated_tts_text(str(item.get("tts_text", "")), comment, stats)
        if why:
            errors.append(f"pk '{pk}': {why} (đã thay bằng bản chuẩn hoá tất định)")

        previous = slots[-1].comment if slots else None
        slots.append(
            Slot(
                comment=comment,
                role=role,  # type: ignore[arg-type]
                tts_text=tts_text,
                est_seconds=estimate_seconds(tts_text, rate),
                reason=str(item.get("reason", ""))[:120]
                or (" · ".join(st.cohesion_reasons(previous, comment, stats)) if previous else "mở màn"),
                replies_to_previous=bool(previous and comment.parent_pk == previous.pk),
            )
        )

    if not slots:
        return [], errors or ["không có phần tử nào dùng được"]

    if slots[0].role != "hook":
        errors.append("phần tử đầu không phải hook")
        slots[0].role = "hook"
    if slots[-1].role != "punchline":
        errors.append("phần tử cuối không phải punchline")
        slots[-1].role = "punchline"

    total = sum(s.est_seconds for s in slots)
    if total > budget_s * DURATION_SLACK:
        errors.append(f"tổng {total:.1f}s vượt ngân sách {budget_s:.1f}s")
        slots = _trim_to_budget(slots, budget_s)

    return slots, errors


def _validated_tts_text(
    proposed: str,
    comment: Comment,
    stats: CorpusStats,
) -> tuple[str, str]:
    """Accept the model's reading only if it is still the same comment."""
    from . import normalize as nz

    deterministic = normalize_for_tts(comment.text)
    candidate = (proposed or "").strip()
    if not candidate:
        return deterministic, ""

    source_norm = nz.norm(comment.text)
    candidate_norm = nz.norm(candidate)

    if nz.token_f1(candidate_norm, source_norm) < MIN_TTS_FIDELITY:
        return deterministic, "tts_text lệch quá xa bản gốc"

    source_tokens = nz.word_set(source_norm)
    invented = [
        t for t in nz.word_set(candidate_norm)
        if t not in source_tokens and stats.rarity(t) > INVENTION_IDF
    ]
    if invented:
        return deterministic, f"tts_text có từ lạ không có trong bản gốc: {invented[:3]}"

    return candidate, ""


def _trim_to_budget(slots: list[Slot], budget_s: float) -> list[Slot]:
    """Drop from the middle -- the hook and the punchline are structural."""
    while len(slots) > 3 and sum(s.est_seconds for s in slots) > budget_s:
        middle = slots[1:-1]
        longest = max(range(len(middle)), key=lambda i: middle[i].est_seconds)
        slots.pop(longest + 1)
    return slots


def curate_with_gemini(
    comments: Sequence[Comment],
    root_pk: str,
    stats: CorpusStats,
    *,
    api_key: str,
    model: str,
    budget_s: float = 50.0,
    rate: float = 1.0,
    timeout_s: float = 60.0,
) -> LlmResult:
    """Heuristic first (it supplies the candidates), then let Gemini re-rank."""
    from .assemble import build_candidates

    baseline = curate_heuristic(comments, root_pk, stats, budget_s=budget_s, rate=rate)
    all_candidates = build_candidates(comments, stats, rate)
    chains = st.rank_chains(st.mine_chains(comments, root_pk), stats)

    # Send the strongest candidates, but never drop one the baseline picked.
    baseline_pks = {s.comment.pk for s in baseline}
    ranked = sorted(
        all_candidates,
        key=lambda c: (c.pk not in baseline_pks, -max(c.feats.shock, c.feats.explain)),
    )[:MAX_CANDIDATES]

    payload = build_payload(ranked, chains, baseline, budget_s)

    try:
        raw = _call_gemini(payload, api_key=api_key, model=model, timeout_s=timeout_s)
    except Exception as exc:  # network, quota, auth -- all degrade the same way
        log.warning("Gemini call failed (%s); dùng kết quả heuristic", exc)
        return LlmResult(slots=baseline, used_llm=False, errors=[str(exc)])

    slots, errors = validate_plan(raw, ranked, stats, budget_s, rate)
    if not slots:
        log.warning("Gemini trả về không dùng được: %s", errors)
        return LlmResult(slots=baseline, used_llm=False, errors=errors)

    if errors:
        log.info("Gemini plan sửa được %d lỗi nhỏ: %s", len(errors), errors[:3])
    log.info("Gemini curation: %d slots, %.1fs", len(slots), sum(s.est_seconds for s in slots))
    return LlmResult(slots=slots, used_llm=True, errors=errors, raw=raw)


def _call_gemini(payload: str, *, api_key: str, model: str, timeout_s: float) -> dict[str, Any]:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model,
        contents=payload,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=RESPONSE_SCHEMA,
            temperature=0.35,
            max_output_tokens=4096,
            http_options=types.HttpOptions(timeout=int(timeout_s * 1000)),
        ),
    )
    text = (response.text or "").strip()
    if not text:
        raise ValueError("Gemini trả về rỗng")
    return json.loads(text)
