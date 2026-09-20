"""Smoke test: render real cards and assert the hard 864x384 constraint.

Run with:  .venv/Scripts/python.exe tests/render_cards_smoke.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image

from threadtrending.cards import theme as T
from threadtrending.cards.fallback_avatar import initials_avatar
from threadtrending.cards.renderer import CardRenderer
from threadtrending.models import CardModel
from threadtrending.scrape.browser import BrowserManager

OUT = Path(__file__).resolve().parent.parent / "output" / "_card_smoke"

CASES: list[tuple[str, CardModel]] = [
    ("short", CardModel(
        username="minh.ng", is_verified=True, timestamp_label="2 giờ",
        like_count=3100, reply_count=42,
        text="Ủa cái gì?! Giải nghệ giữa giải là sao trời ơi 😱",
        avatar_data_uri=initials_avatar("minh.ng"),
    )),
    ("medium_reply", CardModel(
        username="hoanganh_", timestamp_label="1 giờ",
        like_count=2400, reply_count=18, reply_to_username="minh.ng",
        text="Chuyện là team bị tố dàn xếp tỉ số từ vòng bảng, ban tổ chức "
             "đang điều tra nên bạn ấy rút lui trước cho đỡ ảnh hưởng.",
        avatar_data_uri=initials_avatar("hoanganh_"),
    )),
    ("diacritics_emoji", CardModel(
        username="khanhlinh92", timestamp_label="45 phút",
        like_count=890, reply_count=5,
        text="Ế ộ ữ ỡ ẫ ặ ỳ ĐƯỜNG — kiểm tra dấu chồng 🔥😱💀 và emoji màu.",
        avatar_data_uri=initials_avatar("khanhlinh92"),
    )),
    ("very_long", CardModel(
        username="bao.pham", timestamp_label="3 giờ",
        like_count=15400, reply_count=230,
        text=(
            "Mình kể lại đầu đuôi cho mọi người đỡ đoán mò nhé. Hôm thứ ba tuần "
            "trước có một tài khoản ẩn danh đăng đoạn tin nhắn được cho là trong "
            "nhóm chat nội bộ của team, nội dung bàn về việc thả trận ở vòng bảng "
            "để tránh gặp đối thủ mạnh ở bán kết. Ban tổ chức lập tức vào cuộc, "
            "mời cả bốn thành viên lên làm việc trong hai ngày liên tiếp. Đến sáng "
            "nay thì có thông báo chính thức là đình chỉ thi đấu tạm thời trong "
            "lúc chờ kết luận cuối cùng, và ngay sau đó thì bạn ấy đăng status "
            "tuyên bố giải nghệ luôn, không giải thích thêm một lời nào cả."
        ),
        avatar_data_uri=initials_avatar("bao.pham"),
    )),
]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []

    with BrowserManager() as bm:
        renderer = CardRenderer(bm, theme="dark", cache_dir=OUT)
        started = time.time()

        for name, model in CASES:
            pages, font_px = renderer.layout(model)
            print(f"\n[{name}] {len(pages)} page(s) @ font {font_px}px")
            for page in pages:
                png = renderer.render(model, page, font_px, out_dir=OUT)
                width, height = Image.open(png).size
                ok = width == T.CARD_W and height <= T.CARD_MAX_H
                flag = "OK " if ok else "FAIL"
                pct = height / T.CANVAS_H
                print(
                    f"  {flag} page {page.index + 1}/{page.of}: {width}x{height}px "
                    f"({pct:.1%} of canvas height)  {png.name}"
                )
                if not ok:
                    failures.append(f"{name} p{page.index}: {width}x{height}")

        banner = renderer.render_banner("Tuyển thủ PUBG", OUT / "banner.png")
        bw, bh = Image.open(banner).size
        print(f"\n[banner] {bw}x{bh}px (expected {T.BANNER_W}x{T.BANNER_H})")
        if bw != T.BANNER_W or bh > T.BANNER_H + 2:
            failures.append(f"banner: {bw}x{bh}")

        print(f"\nrendered in {time.time() - started:.1f}s -> {OUT}")

    if failures:
        print("\nFAILURES:", *failures, sep="\n  ")
        return 1
    print("\nALL CONSTRAINTS SATISFIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
