"""Unit tests that run offline -- no network, no browser, no ffmpeg.

Run with:  .venv/Scripts/python.exe -m pytest tests/test_core.py -q
"""
from __future__ import annotations

import math
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.fixtures import golden_vi as G
from threadtrending.background.ledger import Ledger
from threadtrending.background.queries import ENERGETIC_POOL, is_calm, rotate, with_topic
from threadtrending.curate import lexicons as lex
from threadtrending.curate import normalize as nz
from threadtrending.curate.assemble import curate_heuristic, plan_roles
from threadtrending.curate.llm import validate_plan
from threadtrending.curate.quality import check, filter_comments
from threadtrending.curate.scoring import CorpusStats, estimate_seconds, features
from threadtrending.curate.structure import cohesion, mine_chains, rank_chains
from threadtrending.curate.tts_text import chunk_for_tts, normalize_for_tts, read_number
from threadtrending.models import Comment
from threadtrending.scrape import sjs
from threadtrending.scrape.tree import build_thread
from threadtrending.scrape.url import InvalidUrl, parse
from threadtrending.tts.tempo import atempo_chain

TREND_URL = (
    "https://www.threads.com/search?q=Tuy%E1%BB%83n%20th%E1%BB%A7%20PUBG"
    "&serp_type=trends&trend_fbid=1072142712480212"
)


# --------------------------------------------------------------------------
# URL parsing
# --------------------------------------------------------------------------
def test_parses_trend_url():
    target = parse(TREND_URL)
    assert target.kind == "trend"
    assert target.query == "Tuyển thủ PUBG"
    assert target.trend_fbid == "1072142712480212"
    assert target.needs_login


def test_parses_permalink_and_does_not_need_login():
    target = parse("https://www.threads.com/@zuck/post/C8vKj2Ap1qX")
    assert (target.kind, target.username, target.post_code) == ("post", "zuck", "C8vKj2Ap1qX")
    assert not target.needs_login


@pytest.mark.parametrize("bad", ["", "https://twitter.com/x", "https://www.threads.com/search"])
def test_rejects_bad_urls(bad):
    with pytest.raises(InvalidUrl):
        parse(bad)


# --------------------------------------------------------------------------
# Reply-forest reconstruction -- the part naive scrapers get wrong
# --------------------------------------------------------------------------
def _item(pk, text="x", user="u", parent=None):
    post = {"pk": pk, "caption": {"text": text}, "user": {"username": user}}
    if parent:
        post["parent_reply_id"] = parent
    return {"post": post}


def test_chain_of_one_is_a_direct_reply():
    thread = build_thread([[_item("1")], [_item("2", parent="1")]], root_pk="1")
    assert [(c.pk, c.depth) for c in thread.replies] == [("2", 1)]


def test_chain_of_four_keeps_its_depths():
    """A length-N thread_items array is a chain, not N siblings."""
    groups = [
        [_item("1")],
        [_item("2"), _item("3", parent="2"), _item("4", parent="3"), _item("5", parent="4")],
    ]
    thread = build_thread(groups, root_pk="1")
    assert [(c.pk, c.depth) for c in thread.replies] == [
        ("2", 1), ("3", 2), ("4", 3), ("5", 4)
    ]
    assert {c.chain_id for c in thread.replies} == {"2"}


def test_chain_position_used_when_parent_id_absent():
    groups = [[_item("1")], [_item("2"), _item("3"), _item("4")]]
    thread = build_thread(groups, root_pk="1")
    assert [(c.pk, c.parent_pk, c.depth) for c in thread.replies] == [
        ("2", "1", 1), ("3", "2", 2), ("4", "3", 3)
    ]


def test_sjs_finds_payload_by_marker_not_by_path():
    html = (
        '<script type="application/json" data-sjs>'
        '{"deeply":{"nested":{"whatever":[{"thread_items":[{"post":{"pk":"9"}}]}]}},'
        '"BarcelonaPostPage":1}</script>'
    )
    payloads = list(sjs.iter_payloads(html, sjs.POST_PAGE_MARKERS))
    assert len(payloads) == 1
    assert sjs.collect_thread_item_groups(payloads[0]) == [[{"post": {"pk": "9"}}]]


def test_dedupe_keeps_the_deeper_expansion():
    short = [_item("2")]
    long = [_item("2"), _item("3", parent="2")]
    assert sjs.dedupe_groups([short, long]) == [long]


# --------------------------------------------------------------------------
# Normalization
# --------------------------------------------------------------------------
def test_detone_handles_d_stroke():
    assert nz.detone("đường") == "duong"
    assert nz.detone("Đức") == "Duc"


def test_teencode_is_whole_token_only():
    assert nz.norm("ko bik j") == "khong biet gi"
    assert "khongo" not in nz.norm("kho hang")


def test_hong_is_not_treated_as_khong():
    """"hóng" de-tones to "hong" and is a load-bearing drama signal."""
    assert lex.lex_hits(nz.norm("ngồi hóng"), lex.REACTION_RE) > 0


def test_lexicon_respects_word_boundaries():
    assert lex.lex_hits(nz.norm("kho hang day"), lex.SHOCK_RE) == 0
    assert lex.lex_hits(nz.norm("hả?"), lex.SHOCK_RE) > 0


def test_jaccard_ignores_punctuation():
    assert nz.jaccard(nz.norm("Chuẩn luôn, khỏi bàn"), nz.norm("Chuẩn luôn khỏi bàn")) == 1.0


# --------------------------------------------------------------------------
# Quality filter
# --------------------------------------------------------------------------
@pytest.mark.parametrize("text,reason", [
    ("Xem thêm tại https://example.com", "link"),
    ("Liên hệ mình 0912345678 nhé", "phone"),
    ("Inbox giá nhé shop uy tín freeship", "spam"),
    ("😱😱😱😱", "emoji_only"),
    ("ừ", "too_short"),
])
def test_rejects_unusable_comments(text, reason):
    assert check(Comment(pk="x", text=text, username="u"), []) == reason


def test_keeps_every_good_comment_in_the_golden_set():
    kept, rejected = filter_comments(
        [c for c in G.comments() if c.depth > 0], root_username="tinthethao24h"
    )
    labels = G.labelled()
    good = {"hook", "explain", "counter", "reaction", "punchline"}
    assert not [r for r in rejected if labels[r.comment.pk] in good]
    assert len(kept) == 9


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------
def _golden_scored():
    kept, _ = filter_comments(
        [c for c in G.comments() if c.depth > 0], root_username="tinthethao24h"
    )
    stats = CorpusStats.build(kept)
    return kept, stats, {c.pk: features(c, stats) for c in kept}


def test_hook_ranks_top_on_shock():
    kept, _stats, feats = _golden_scored()
    ranked = sorted(feats, key=lambda pk: -feats[pk].shock)
    hooks = {pk for pk, role in G.labelled().items() if role == "hook"}
    assert set(ranked[:2]) == hooks


def test_explanations_sweep_the_top_of_explain():
    kept, _stats, feats = _golden_scored()
    ranked = sorted(feats, key=lambda pk: -feats[pk].explain)
    explains = {pk for pk, role in G.labelled().items() if role == "explain"}
    assert set(ranked[:3]) == explains


def test_duration_estimate_tracks_syllables():
    assert estimate_seconds("một hai ba bốn năm", 1.0) == pytest.approx(1.0 + 0.35, abs=0.01)
    fast = estimate_seconds("một hai ba bốn năm", 1.25)
    assert fast < estimate_seconds("một hai ba bốn năm", 1.0)


# --------------------------------------------------------------------------
# Structure and assembly
# --------------------------------------------------------------------------
def test_real_reply_beats_same_author_monologue():
    kept, stats, _ = _golden_scored()
    by_pk = {c.pk: c for c in kept}
    reply = cohesion(by_pk["2"], by_pk["3"], stats)
    monologue = cohesion(by_pk["3"], by_pk["5"], stats)  # same author, not a reply
    assert reply > 1.0 > monologue


def test_hottest_chain_is_the_two_person_argument():
    kept, stats, _ = _golden_scored()
    chains = rank_chains(mine_chains(kept, "1"), stats)
    assert chains[0].pks == ("2", "3", "4", "5")
    assert chains[0].n_speakers == 2


def test_plan_starts_with_hook_and_ends_with_punchline():
    for n in range(6, 13):
        roles = plan_roles(n)
        assert (roles[0], roles[-1], len(roles)) == ("hook", "punchline", n)


def test_sequence_opens_with_a_hook_and_chains_real_replies():
    kept, stats, _ = _golden_scored()
    slots = curate_heuristic(kept, "1", stats, budget_s=50.0)
    assert slots[0].role == "hook"
    assert slots[-1].role == "punchline"
    assert G.labelled()[slots[0].comment.pk] == "hook"
    # The point of the whole exercise: consecutive comments that really replied.
    assert sum(1 for s in slots if s.replies_to_previous) >= 3


def test_sequence_respects_the_duration_budget():
    kept, stats, _ = _golden_scored()
    slots = curate_heuristic(kept, "1", stats, budget_s=18.0)
    assert sum(s.est_seconds for s in slots) <= 18.0


# --------------------------------------------------------------------------
# LLM response validation
# --------------------------------------------------------------------------
def _candidates():
    from threadtrending.curate.assemble import build_candidates

    kept, stats, _ = _golden_scored()
    return build_candidates(kept, stats), stats


def test_accepts_a_well_formed_plan():
    cands, stats = _candidates()
    raw = {"sequence": [
        {"pk": "2", "role": "hook", "tts_text": "Ủa cái gì? Giải nghệ giữa giải là sao trời ơi.", "est_seconds": 2.6},
        {"pk": "3", "role": "explain", "tts_text": "Chuyện là team bị tố dàn xếp tỉ số từ vòng bảng.", "est_seconds": 4.0},
        {"pk": "10", "role": "punchline", "tts_text": "Thôi xong, drama này còn dài.", "est_seconds": 1.5},
    ], "total_est_seconds": 8.1}
    slots, errors = validate_plan(raw, cands, stats, 50.0)
    assert [s.comment.pk for s in slots] == ["2", "3", "10"]
    assert errors == []


def test_rejects_invented_text_and_unknown_pks():
    cands, stats = _candidates()
    raw = {"sequence": [
        {"pk": "2", "role": "hook",
         "tts_text": "Cầu thủ đó đã bị bắt giam tại Singapore vì rửa tiền xuyên quốc gia.",
         "est_seconds": 5.0},
        {"pk": "9999", "role": "explain", "tts_text": "không tồn tại", "est_seconds": 2.0},
        {"pk": "3", "role": "explain", "tts_text": "Chuyện là team bị tố dàn xếp tỉ số.", "est_seconds": 3.0},
        {"pk": "3", "role": "reaction", "tts_text": "trùng", "est_seconds": 1.0},
        {"pk": "10", "role": "punchline", "tts_text": "Thôi xong, drama này còn dài.", "est_seconds": 1.5},
    ], "total_est_seconds": 12.5}
    slots, errors = validate_plan(raw, cands, stats, 50.0)

    assert [s.comment.pk for s in slots] == ["2", "3", "10"]      # unknown + dup dropped
    assert "Singapore" not in slots[0].tts_text                   # invention replaced
    assert any("không có trong CANDIDATES" in e for e in errors)
    assert any("lệch quá xa" in e for e in errors)


# --------------------------------------------------------------------------
# Read-aloud normalization
# --------------------------------------------------------------------------
@pytest.mark.parametrize("value,expected", [
    (0, "không"), (15, "mười lăm"), (21, "hai mươi mốt"), (24, "hai mươi tư"),
    (105, "một trăm linh năm"), (1005, "một nghìn không trăm linh năm"),
    (2_500_000, "hai triệu năm trăm nghìn"), (1_000_000_000, "một tỷ"),
])
def test_reads_vietnamese_numbers(value, expected):
    assert read_number(value) == expected


def test_strips_unspeakable_and_expands_shorthand():
    out = normalize_for_tts("ko bik j @minh https://x.co #drama 15k lượt 😱")
    assert "@" not in out and "http" not in out and "#" not in out
    assert "không biết gì" in out and "mười lăm nghìn" in out


def test_money_shorthand():
    assert "hai triệu năm trăm nghìn" in normalize_for_tts("giá 2tr5 thôi")


def test_chunks_never_exceed_the_cap():
    text = "Chuyện là hôm qua team bị tố dàn xếp tỉ số từ vòng bảng. " * 5
    chunks = chunk_for_tts(text, max_syllables=20)
    assert chunks and all(len(c.split()) <= 20 for c in chunks)
    assert "".join(chunks).replace(" ", "") == text.replace(" ", "").rstrip()


# --------------------------------------------------------------------------
# Audio tempo
# --------------------------------------------------------------------------
@pytest.mark.parametrize("rate,stages", [(1.0, 0), (1.15, 1), (2.5, 2), (0.4, 2)])
def test_atempo_stays_within_the_legal_range(rate, stages):
    chain = atempo_chain(rate)
    assert len(chain) == stages
    product = math.prod(float(c.split("=")[1]) for c in chain) if chain else 1.0
    assert product == pytest.approx(rate, rel=0.02)
    assert all(0.5 <= float(c.split("=")[1]) <= 2.0 for c in chain)


# --------------------------------------------------------------------------
# Background ledger
# --------------------------------------------------------------------------
def test_reservation_is_released_but_confirmation_is_permanent():
    with tempfile.TemporaryDirectory() as td:
        ledger = Ledger(Path(td) / "l.sqlite3")

        row = ledger.reserve("pexels", "1", file_url="u", query="q", run_id="r")
        assert ledger.reserve("pexels", "1", file_url="u", query="q", run_id="r2") is None
        ledger.release(row)
        assert "1" not in ledger.blocked_ids("pexels")

        row = ledger.reserve("pexels", "2", file_url="u", query="q", run_id="r")
        ledger.mark(row, "confirmed", content_sha256="abc")
        ledger.release(row)
        assert "2" in ledger.blocked_ids("pexels")
        assert ledger.hash_seen("abc")


def test_low_motion_rejection_is_sticky():
    with tempfile.TemporaryDirectory() as td:
        ledger = Ledger(Path(td) / "l.sqlite3")
        row = ledger.reserve("pexels", "3", file_url="u", query="q", run_id="r")
        ledger.mark(row, "rejected_low_motion", motion_score=1.2)
        assert "3" in ledger.blocked_ids("pexels")


# --------------------------------------------------------------------------
# Background queries
# --------------------------------------------------------------------------
def test_query_pool_rotates_widely():
    assert len({rotate(i) for i in range(40)}) >= 15


def test_topic_injection_only_takes_whole_ascii_words():
    # Acronyms win over title-case words: "PUBG" describes imagery, "Tuyển" is
    # a fragment we must never search for.
    assert with_topic("race car", "Tuyển thủ PUBG") == "race car pubg"
    assert with_topic("race car", "VTV đưa tin Concert") == "race car vtv"
    # Every word carries a diacritic -> nothing to inject, base query untouched.
    assert with_topic("race car", "Giá vàng tăng kỷ lục") == "race car"


def test_calm_slugs_are_rejected():
    assert is_calm("https://pexels.com/video/relaxing-ocean-sunset-123")
    assert not is_calm("https://pexels.com/video/street-dance-crew-456")


def test_pool_contains_no_calm_terms():
    assert not [q for q in ENERGETIC_POOL if is_calm(q)]
