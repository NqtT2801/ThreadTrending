"""Vietnamese marker lexicons for the narrative-role scorers.

Terms are written *with* diacritics because that is what a human can maintain,
and compiled into de-toned, word-boundary regexes at import time -- matching runs
against :func:`..normalize.norm` output.

Word boundaries matter more than they look: "hả" de-tones to "ha", and a plain
substring test would then fire on "hàng", "hahaha" and "thảm hại".
"""
from __future__ import annotations

import re
from typing import Iterable, Mapping

from .normalize import detone

Lexicon = Mapping[float, Iterable[str]]

SHOCK: Lexicon = {
    # tier 3 -- hard shock, drama reveal
    3.0: [
        "sốc", "choáng", "xỉu ngang", "xỉu dọc", "đứng hình", "bay màu", "toang",
        "xong phim", "bóc phốt", "lộ clip", "lộ tin nhắn", "cạn lời",
        "không thể tin nổi", "lật kèo", "quay xe", "căng đét", "phốt", "drama",
        "gây sốc", "chấn động", "kinh khủng", "khủng khiếp",
    ],
    # tier 2 -- strong reaction, discovery
    2.0: [
        "trời ơi", "ối giời ơi", "trời đất", "á đù", "vãi", "vl", "vcl", "vkl",
        "khét", "căng", "hết nước chấm", "khó đỡ", "hóa ra", "té ra", "ai ngờ",
        "không ngờ", "thì ra", "mới biết", "bị lộ", "bị bóc", "công khai",
        "nổ ra", "ghê thật", "kinh dị", "nghe mà sợ",
    ],
    # tier 1 -- mild surprise, attention grab
    1.0: [
        "ủa", "hả", "cái gì", "gì vậy", "thật á", "thật hả", "thật sự luôn",
        "trời", "ghê", "bất ngờ", "gãy", "cháy", "sập", "tuột mood", "đọc mà sợ",
    ],
}

EXPLAIN: Lexicon = {
    3.0: [
        "chuyện là", "sự việc là", "đầu đuôi là", "nguyên nhân", "lý do là",
        "cụ thể là", "diễn biến", "tóm lại là", "giải thích", "nói rõ hơn",
        "tình hình là", "được biết", "sự thật là",
    ],
    2.0: [
        "là vì", "bởi vì", "tại vì", "do là", "thực ra", "thật ra", "thực chất",
        "theo như", "theo mình biết", "mình hóng được", "mình có theo dõi",
        "mình đọc được", "nguồn là", "update", "cập nhật", "kể lại",
        "dẫn đến", "dẫn tới", "cho nên", "vì thế", "nói chung là",
    ],
    1.0: [
        "sau đó", "trước đó", "ban đầu", "rồi thì", "cuối cùng", "lúc đó",
        "hồi đó", "hôm qua", "hôm nay", "sáng nay", "tối qua", "mấy hôm trước",
        "hình như là", "nghe nói", "được cho là",
    ],
}

COUNTER: Lexicon = {
    3.0: [
        "sai rồi", "không đúng", "chưa chắc", "đừng vội", "nghe một phía",
        "một chiều", "chưa có bằng chứng", "khách quan mà nói", "ngược lại thì",
        "đừng phán xét", "chưa đủ thông tin",
    ],
    2.0: [
        "nhưng mà", "tuy nhiên", "ngược lại", "đâu có", "làm gì có", "bênh vực",
        "thực tế thì", "mình thấy ngược lại", "hóng thêm đã", "đợi đã",
        "bình tĩnh", "công bằng mà nói",
    ],
    1.0: ["nhưng", "gì mà", "chứ bộ", "đâu mà", "có đâu", "ai bảo"],
}

REACTION: Lexicon = {
    2.0: [
        "hóng", "hóng quá", "ngồi hóng", "mang bỏng ra hóng", "chuẩn luôn",
        "quá chuẩn", "y chang", "đúng rồi", "chịu", "bó tay", "thôi xong",
        "cười ỉa", "cười xỉu", "đỉnh", "khỏi bàn",
    ],
    1.0: [
        "haha", "hahaha", "hihi", "hehe", "kkk", "kk", "cười", "chuẩn",
        "đồng ý", "same", "thế à", "ok", "oke",
    ],
}

#: Substring-matched (not word-bounded): ad copy is deliberately misspelled.
SPAM: tuple[str, ...] = (
    "inbox giá", "ib giá", "liên hệ zalo", "zalo", "shopee", "săn sale",
    "mã giảm", "giảm giá", "freeship", "order", "sỉ lẻ", "nhà cái", "tài xỉu",
    "uy tín", "đặt cược", "kèo thơm", "vay nhanh", "lãi suất", "tuyển ctv",
    "việc làm tại nhà", "kiếm tiền online", "follow mình", "theo dõi mình",
    "add zalo", "check inbox", "xem bio", "link bio", "hàng chính hãng",
)

SHOCK_EMOJI = frozenset("😱😭💀🤯🔥😳🤬‼❗😨😰🫢🤡⚡")
LAUGH_EMOJI = frozenset("🤣😂😆😹")

#: Phrases that refer back to what was just said -- the textual trace of a reply.
ANAPHORA_TERMS: tuple[str, ...] = (
    "đúng rồi", "chuẩn", "sai rồi", "ý bạn là", "bạn nói", "cái này", "vụ này",
    "chuyện này", "ủa nhưng", "thế thì", "vậy thì", "như bạn", "nghe bạn",
    "theo bạn", "đồng ý", "không đúng đâu", "ai bảo", "thế mà", "vậy mà",
    "như trên", "bên trên", "comment trên", "bạn kia",
)


def _compile(terms: Iterable[str]) -> re.Pattern[str]:
    """Word-bounded alternation over de-toned terms, longest-first."""
    detoned = sorted({detone(t.lower()) for t in terms}, key=len, reverse=True)
    if not detoned:
        return re.compile(r"(?!)")
    body = "|".join(re.escape(t) for t in detoned)
    return re.compile(rf"(?<![0-9a-z])(?:{body})(?![0-9a-z])")


def _compile_lexicon(lex: Lexicon) -> tuple[tuple[float, re.Pattern[str]], ...]:
    return tuple((weight, _compile(terms)) for weight, terms in lex.items())


SHOCK_RE = _compile_lexicon(SHOCK)
EXPLAIN_RE = _compile_lexicon(EXPLAIN)
COUNTER_RE = _compile_lexicon(COUNTER)
REACTION_RE = _compile_lexicon(REACTION)
ANAPHORA_RE = _compile(ANAPHORA_TERMS)
SPAM_DETONED: tuple[str, ...] = tuple(detone(s.lower()) for s in SPAM)


def lex_hits(normalized: str, compiled: tuple[tuple[float, re.Pattern[str]], ...]) -> float:
    """Total weight of matched terms. Input must already be :func:`norm`-ed."""
    return sum(weight * len(pattern.findall(normalized)) for weight, pattern in compiled)


def is_spam(normalized: str) -> bool:
    return any(term in normalized for term in SPAM_DETONED)


def count_emoji(text: str, table: frozenset[str]) -> int:
    return sum(1 for ch in text if ch in table)
