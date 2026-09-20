"""A hand-labelled Vietnamese comment set used to keep the scorers honest.

``role`` is the role a human would assign. The scorers are not expected to agree
on every single item -- they are expected to rank the labelled ``hook`` into the
top of ``shock_score`` and the labelled ``explain`` items into the top of
``explain_score``.
"""
from __future__ import annotations

from threadtrending.models import Comment

# (pk, parent, username, likes, taken_at, role, text)
RAW: list[tuple[str, str | None, str, int, int, str, str]] = [
    ("1", None, "tinthethao24h", 4200, 1_758_300_000, "root",
     "Tuyển thủ PUBG số 1 Việt Nam bất ngờ tuyên bố giải nghệ ngay giữa giải đấu"),

    ("2", "1", "minh.ng", 3100, 1_758_300_120, "hook",
     "Ủa cái gì?! Giải nghệ giữa giải là sao trời ơi 😱"),
    ("3", "2", "hoanganh_", 2400, 1_758_300_300, "explain",
     "Chuyện là team bị tố dàn xếp tỉ số từ vòng bảng, ban tổ chức đang điều tra nên bạn ấy rút lui"),
    ("4", "3", "minh.ng", 1800, 1_758_300_500, "counter",
     "Chưa chắc đâu, nghe một phía thôi. Chưa có bằng chứng gì mà đã kết luận rồi"),
    ("5", "4", "hoanganh_", 1500, 1_758_300_800, "explain",
     "Thật ra BTC đã ra thông báo chính thức lúc 9 giờ sáng nay rồi, có cả biên bản luôn"),

    ("6", "1", "khanhlinh92", 2900, 1_758_300_200, "explain",
     "Đầu đuôi là hôm qua có người tung đoạn tin nhắn trong nhóm chat của team ra ngoài"),
    ("7", "6", "duc.tran", 900, 1_758_300_400, "reaction",
     "Ngồi hóng tiếp 🍿"),

    ("8", "1", "bao.pham", 2100, 1_758_300_260, "hook",
     "CẠN LỜI. Mình theo dõi bạn này 5 năm rồi mà giờ bay màu kiểu này"),
    ("9", "1", "vy.nguyen", 1200, 1_758_300_600, "reaction",
     "Chuẩn luôn, khỏi bàn"),
    ("10", "1", "quangminh", 700, 1_758_300_700, "punchline",
     "Thôi xong, drama này còn dài"),

    # --- items the quality filter must reject ---
    ("90", "1", "shop_gia_re", 5, 1_758_300_900, "spam",
     "Inbox giá nhé shop uy tín freeship toàn quốc"),
    ("91", "1", "spam2", 3, 1_758_300_910, "spam",
     "Xem thêm tại https://example.com/abc nhé mọi người"),
    ("92", "1", "emoji_guy", 40, 1_758_300_920, "emoji_only",
     "😱😱😱😱"),
    ("93", "1", "short_guy", 8, 1_758_300_930, "too_short",
     "ừ"),
    ("94", "1", "dupe", 60, 1_758_300_940, "dup",
     "Chuẩn luôn khỏi bàn"),
    ("95", "1", "phoner", 2, 1_758_300_950, "phone",
     "Liên hệ mình 0912345678 để biết thêm chi tiết nha"),
]


def comments() -> list[Comment]:
    out: list[Comment] = []
    for pk, parent, user, likes, taken, _role, text in RAW:
        depth = 0 if parent is None else 1
        out.append(
            Comment(
                pk=pk,
                text=text,
                username=user,
                like_count=likes,
                taken_at=taken,
                parent_pk=parent,
                depth=depth,
                chain_id="" if parent is None else pk,
            )
        )
    return out


def labelled() -> dict[str, str]:
    return {pk: role for pk, _p, _u, _l, _t, role, _x in RAW}


def groups() -> list[list[dict]]:
    """The same data shaped like Threads ``thread_items`` chains."""
    by_parent: dict[str | None, list[tuple]] = {}
    for row in RAW:
        by_parent.setdefault(row[1], []).append(row)

    def item(row: tuple) -> dict:
        pk, parent, user, likes, taken, _role, text = row
        post = {
            "pk": pk,
            "caption": {"text": text},
            "user": {"username": user, "profile_pic_url": "", "is_verified": False},
            "like_count": likes,
            "taken_at": taken,
        }
        if parent:
            post["parent_reply_id"] = parent
        return {"post": post}

    out: list[list[dict]] = [[item(r) for r in by_parent[None]]]
    for row in by_parent.get("1", []):
        chain = [item(row)]
        cursor = row[0]
        while children := by_parent.get(cursor):
            chain.append(item(children[0]))
            cursor = children[0][0]
        out.append(chain)
    return out
