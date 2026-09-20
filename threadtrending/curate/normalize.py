"""Text normalization for Vietnamese social-media comments.

Lexicon matching happens on a *de-toned, teencode-expanded* form so that
"trời ơi", "troi oi" and "tr oi" all hit the same entry. Two subtleties that
cost real debugging time:

* ``unicodedata.normalize("NFD", ...)`` does **not** decompose ``đ``/``Đ`` -- they
  are distinct letters, not d-with-a-mark -- so they need an explicit mapping.
* Teencode substitution must be whole-token only. A substring rule turns "kho"
  into "khôngo" and "không" into "khôngg".
"""
from __future__ import annotations

import functools
import re
import unicodedata

import emoji as emoji_lib

URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.I)
MENTION_RE = re.compile(r"@[A-Za-z0-9._]+")
HASHTAG_RE = re.compile(r"#[^\s#]+")
PHONE_RE = re.compile(r"(?:(?:\+?84)|0)(?:\d[ .\-]?){8,10}\d")
MENTION_ONLY_RE = re.compile(r"^(?:\s*@[A-Za-z0-9._]+)+\s*$")
ELONGATION_RE = re.compile(r"(.)\1{2,}")
WS_RE = re.compile(r"\s+")
CAPS_RUN_RE = re.compile(r"[A-ZÀ-Ỹ]{3,}")
LATIN_RE = re.compile(r"[A-Za-zÀ-ỹĐđ]")

#: Whole-token expansions only. Keys are matched after lowercasing, before detone.
TEENCODE: dict[str, str] = {
    # "hong"/"hông" deliberately absent: they are also the de-toned form of
    # "hóng" (to lurk on drama), which is a load-bearing signal in this domain.
    "k": "không", "ko": "không", "kg": "không", "hok": "không",
    "khong": "không", "kh": "không", "hem": "không",
    "dc": "được", "đc": "được", "dk": "được", "duoc": "được", "đuoc": "được",
    "j": "gì", "ji": "gì", "gi": "gì",
    "z": "vậy", "v": "vậy", "vại": "vậy", "zậy": "vậy", "dzậy": "vậy",
    "bik": "biết", "bít": "biết", "bit": "biết", "biet": "biết",
    "mik": "mình", "mk": "mình", "mih": "mình", "minh": "mình",
    "m": "mày", "t": "tao", "tui": "tôi", "tớ": "tôi",
    "ny": "người yêu", "nyc": "người yêu cũ", "ck": "chồng", "vk": "vợ",
    "đt": "điện thoại", "dt": "điện thoại", "fb": "phây", "ib": "nhắn tin riêng",
    "inb": "nhắn tin riêng", "inbox": "nhắn tin riêng",
    "cmt": "bình luận", "cmnt": "bình luận", "tks": "cảm ơn", "thks": "cảm ơn",
    "vs": "với", "w": "với", "r": "rồi", "rùi": "rồi", "òi": "rồi", "ròi": "rồi",
    "nhg": "nhưng", "nhưg": "nhưng", "trc": "trước", "sn": "sinh nhật",
    "stt": "status", "ns": "nói", "nx": "nữa", "h": "giờ",
    "bn": "bạn", "b": "bạn", "ae": "anh em", "cv": "công việc",
    "ex": "người yêu cũ", "ctv": "cộng tác viên", "sp": "sản phẩm",
    "vn": "việt nam", "hqua": "hôm qua", "hnay": "hôm nay",
}


@functools.lru_cache(maxsize=8192)
def detone(s: str) -> str:
    """Strip Vietnamese tone marks and fold ``đ`` -> ``d``."""
    decomposed = unicodedata.normalize("NFD", s)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return (
        unicodedata.normalize("NFC", stripped)
        .replace("đ", "d")
        .replace("Đ", "D")
    )


def strip_emoji(s: str) -> str:
    return emoji_lib.replace_emoji(s, replace=" ")


def expand_teencode(s: str) -> str:
    return " ".join(TEENCODE.get(tok, tok) for tok in s.split())


@functools.lru_cache(maxsize=8192)
def norm(s: str) -> str:
    """The canonical matching form: lowercase, no emoji/URL/@, teencode expanded, de-toned."""
    s = unicodedata.normalize("NFC", s).lower()
    s = URL_RE.sub(" ", s)
    s = MENTION_RE.sub(" ", s)
    s = strip_emoji(s)
    s = ELONGATION_RE.sub(r"\1\1", s)  # "haaaaa" -> "haa": keeps the signal, tames the token
    s = expand_teencode(s)
    return WS_RE.sub(" ", detone(s)).strip()


def syllables(s: str) -> int:
    """Whitespace token count.

    Vietnamese is written one syllable per token, so this doubles as the TTS
    duration predictor used throughout :mod:`..curate.scoring`.
    """
    return len(strip_emoji(URL_RE.sub(" ", s)).split())


def graphemes(s: str) -> int:
    """Approximate user-perceived character count (combining marks don't count)."""
    return sum(1 for c in unicodedata.normalize("NFC", s) if not unicodedata.combining(c))


def emoji_ratio(s: str) -> float:
    visible = graphemes(WS_RE.sub("", s))
    if visible == 0:
        return 1.0
    return min(1.0, emoji_lib.emoji_count(s) / visible)


def caps_run_ratio(s: str) -> float:
    """Longest ALL-CAPS run as a fraction of the text -- a shouting signal."""
    letters = graphemes(WS_RE.sub("", s))
    if letters == 0:
        return 0.0
    runs = CAPS_RUN_RE.findall(s)
    return min(1.0, (max((len(r) for r in runs), default=0)) / letters)


def latin_ratio(s: str) -> float:
    visible = graphemes(WS_RE.sub("", s))
    if visible == 0:
        return 0.0
    return len(LATIN_RE.findall(s)) / visible


def tokens(s: str) -> list[str]:
    return norm(s).split()


PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def word_set(s: str) -> set[str]:
    """Punctuation-free token set.

    Without this, "chuan luon, khoi ban" and "chuan luon khoi ban" share only
    three of five tokens and a real duplicate slips through the filter.
    """
    return set(PUNCT_RE.sub(" ", s).split())


def jaccard(a: str, b: str) -> float:
    """Token-set Jaccard on normalized text; used for near-duplicate rejection."""
    sa, sb = word_set(a), word_set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def token_f1(a: str, b: str) -> float:
    """Token-set F1. Guards LLM ``tts_text`` against drifting off the original."""
    sa, sb = word_set(a), word_set(b)
    if not sa or not sb:
        return 0.0
    overlap = len(sa & sb)
    if overlap == 0:
        return 0.0
    precision, recall = overlap / len(sa), overlap / len(sb)
    return 2 * precision * recall / (precision + recall)
