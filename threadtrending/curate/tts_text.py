"""Turn a raw comment into something a TTS voice can read out loud.

This is needed unconditionally: it is the no-LLM path, and it is also the
per-item fallback when the LLM returns a ``tts_text`` that fails the fidelity
check. Meaning is never altered -- teencode is expanded, digits are spelled, and
anything unspeakable (emoji, URLs, @handles) is dropped.
"""
from __future__ import annotations

import re
import unicodedata

from . import normalize as nz

UNITS = ("không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín")
SCALES = ("", " nghìn", " triệu", " tỷ")

#: Suffixes people type instead of writing the number out.
MAGNITUDE_SUFFIX = {
    "k": "nghìn", "K": "nghìn",
    "tr": "triệu", "Tr": "triệu", "TR": "triệu",
    "m": "triệu", "M": "triệu",
    "b": "tỷ", "B": "tỷ", "ty": "tỷ", "tỷ": "tỷ", "tỉ": "tỷ",
}

UNIT_WORD = {
    "%": "phần trăm", "$": "đô la", "k": "nghìn",
    "h": "giờ", "p": "phút", "s": "giây",
    "kg": "ki lô gam", "km": "ki lô mét", "m2": "mét vuông",
}

#: Read-aloud expansions that are not teencode, just abbreviations.
ABBREVIATIONS = {
    "vn": "Việt Nam", "tphcm": "thành phố Hồ Chí Minh", "hn": "Hà Nội",
    "ctv": "cộng tác viên", "btc": "ban tổ chức", "clb": "câu lạc bộ",
    "vđv": "vận động viên", "hlv": "huấn luyện viên", "cđv": "cổ động viên",
    "mxh": "mạng xã hội", "pubg": "pắp ghi", "esport": "i sport",
}

SOFTEN = {
    "vl": "vãi", "vcl": "vãi", "vkl": "vãi", "vc": "vãi",
    "đm": "trời", "dm": "trời", "clm": "trời", "cc": "gì",
    "đmm": "trời ơi", "vlone": "vãi",
}

PUNCT_COLLAPSE_RE = re.compile(r"([.!?,;:])\1+")
MULTI_PUNCT_RE = re.compile(r"[!?]{2,}")
SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([.!?,;:])")
NUMBER_WITH_SUFFIX_RE = re.compile(
    r"(?<![\w])(\d[\d.,]*)\s*(k|K|tr|Tr|TR|m|M|b|B|ty|tỷ|tỉ|%|h|p|s|kg|km)?(?![\w])"
)
DANGLING_RE = re.compile(r"^[\s\-–—•*.,;:]+|[\s\-–—•*]+$")
#: Vietnamese money shorthand where the magnitude sits mid-number: "2tr5" = 2.5
#: million, "3k5" = 3500, "1ty2" = 1.2 billion.
SPLIT_MAGNITUDE_RE = re.compile(r"(?<![\w])(\d+)\s*(tr|k|ty|tỷ|tỉ)\s*(\d+)(?![\w])", re.I)
_MAGNITUDE_VALUE = {"k": 1_000, "tr": 1_000_000, "ty": 1_000_000_000,
                    "tỷ": 1_000_000_000, "tỉ": 1_000_000_000}
ELLIPSIS_RE = re.compile(r"\.{3,}|…")


def _read_three(n: int, force_hundreds: bool) -> str:
    """Read a 0..999 group. ``force_hundreds`` applies to non-leading groups."""
    hundreds, rest = divmod(n, 100)
    tens, units = divmod(rest, 10)
    parts: list[str] = []

    if hundreds or force_hundreds:
        parts.append(f"{UNITS[hundreds]} trăm")

    if tens == 0:
        if units:
            # "một trăm linh năm", but plain "năm" when there is no hundred part
            parts.append(f"linh {UNITS[units]}" if parts else UNITS[units])
    elif tens == 1:
        parts.append("mười" if units != 5 else "mười lăm")
        if units and units != 5:
            parts.append(UNITS[units])
    else:
        parts.append(f"{UNITS[tens]} mươi")
        if units == 1:
            parts.append("mốt")
        elif units == 4:
            parts.append("tư")
        elif units == 5:
            parts.append("lăm")
        elif units:
            parts.append(UNITS[units])

    return " ".join(parts)


def read_number(value: int) -> str:
    """Spell an integer in Vietnamese, up to the billions."""
    if value < 0:
        return "âm " + read_number(-value)
    if value == 0:
        return "không"
    if value >= 1_000_000_000_000:
        return str(value)  # beyond what a comment ever means; leave the digits

    groups: list[int] = []
    while value:
        value, remainder = divmod(value, 1000)
        groups.append(remainder)

    out: list[str] = []
    for index in range(len(groups) - 1, -1, -1):
        group = groups[index]
        if group == 0:
            continue
        is_leading = index == len(groups) - 1
        out.append(_read_three(group, force_hundreds=not is_leading) + SCALES[index])
    return " ".join(out).strip()


def _expand_split_magnitude(match: re.Match[str]) -> str:
    whole, unit, fraction = match.group(1), match.group(2).lower(), match.group(3)
    scale = _MAGNITUDE_VALUE.get(unit)
    if scale is None or len(fraction) > 3:
        return match.group(0)
    value = int(whole) * scale + int(fraction) * (scale // 10 ** len(fraction))
    return read_number(value)


def _expand_number(match: re.Match[str]) -> str:
    raw, suffix = match.group(1), match.group(2)
    digits = raw.replace(".", "").replace(",", "")
    if not digits.isdigit() or len(digits) > 12:
        return match.group(0)

    spoken = read_number(int(digits))
    if not suffix:
        return spoken
    if suffix in MAGNITUDE_SUFFIX and suffix not in ("%",):
        return f"{spoken} {MAGNITUDE_SUFFIX[suffix]}"
    return f"{spoken} {UNIT_WORD.get(suffix, suffix)}"


def _expand_tokens(text: str, *, soften: bool) -> str:
    out: list[str] = []
    for token in text.split():
        bare = token.strip(".,!?;:\"'()[]").lower()
        replacement = None
        if soften and bare in SOFTEN:
            replacement = SOFTEN[bare]
        elif bare in ABBREVIATIONS:
            replacement = ABBREVIATIONS[bare]
        elif bare in nz.TEENCODE:
            replacement = nz.TEENCODE[bare]

        if replacement is None:
            out.append(token)
            continue
        # Keep any trailing punctuation the original token carried.
        tail = token[len(token.rstrip(".,!?;:")) :] if token.rstrip(".,!?;:") else ""
        out.append(replacement + tail)
    return " ".join(out)


def normalize_for_tts(text: str, *, soften_profanity: bool = True) -> str:
    """Read-aloud form of ``text``. Word order and register are preserved."""
    s = unicodedata.normalize("NFC", text or "").strip()
    if not s:
        return ""

    s = nz.URL_RE.sub(" ", s)
    s = nz.MENTION_RE.sub(" ", s)
    s = nz.HASHTAG_RE.sub(" ", s)
    s = nz.strip_emoji(s)
    s = ELLIPSIS_RE.sub(". ", s)
    s = nz.ELONGATION_RE.sub(r"\1", s)

    s = _expand_tokens(s, soften=soften_profanity)
    # Split-magnitude first: NUMBER_WITH_SUFFIX_RE would otherwise eat "2tr" and
    # leave a stray "5" behind.
    s = SPLIT_MAGNITUDE_RE.sub(_expand_split_magnitude, s)
    s = NUMBER_WITH_SUFFIX_RE.sub(_expand_number, s)

    s = MULTI_PUNCT_RE.sub(lambda m: m.group(0)[0], s)
    s = PUNCT_COLLAPSE_RE.sub(r"\1", s)
    s = SPACE_BEFORE_PUNCT_RE.sub(r"\1", s)
    s = nz.WS_RE.sub(" ", s)
    s = DANGLING_RE.sub("", s).strip()

    if s and s[-1] not in ".!?":
        s += "."
    return s


def chunk_for_tts(text: str, max_syllables: int = 55) -> list[str]:
    """Split a long comment at speech-natural boundaries.

    Page breaks in the card renderer are aligned to these chunks, so audio and
    visuals advance together. Priority: sentence enders, then clause enders,
    then commas followed by a connective. Never mid-word.
    """
    text = (text or "").strip()
    if not text or nz.syllables(text) <= max_syllables:
        return [text] if text else []

    sentences = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()]
    pieces: list[str] = []
    for sentence in sentences:
        if nz.syllables(sentence) <= max_syllables:
            pieces.append(sentence)
            continue
        clauses = [p.strip() for p in re.split(r"(?<=[;:])\s+", sentence) if p.strip()]
        for clause in clauses:
            if nz.syllables(clause) <= max_syllables:
                pieces.append(clause)
            else:
                pieces.extend(_split_on_commas(clause, max_syllables))

    return _repack(pieces, max_syllables)


def _split_on_commas(text: str, max_syllables: int) -> list[str]:
    parts = [p.strip() for p in re.split(r",\s*", text) if p.strip()]
    out: list[str] = []
    for part in parts:
        if nz.syllables(part) <= max_syllables:
            out.append(part)
        else:
            out.extend(_split_on_words(part, max_syllables))
    return out


def _split_on_words(text: str, max_syllables: int) -> list[str]:
    words = text.split()
    return [
        " ".join(words[i : i + max_syllables])
        for i in range(0, len(words), max_syllables)
    ]


def _repack(pieces: list[str], max_syllables: int) -> list[str]:
    """Greedily merge adjacent pieces so we do not emit lots of tiny cards."""
    out: list[str] = []
    for piece in pieces:
        if out and nz.syllables(out[-1] + " " + piece) <= max_syllables:
            out[-1] = f"{out[-1]} {piece}".strip()
        else:
            out.append(piece)
    return out
