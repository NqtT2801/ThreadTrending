# ThreadTrending

Dán một link trend của Threads → nhận về **một video dọc 1080×1920** kể lại vụ việc
bằng chính bình luận của mọi người, sẵn sàng đăng TikTok / Reels / Shorts.

Video gồm ba lớp: **video nền sôi nổi** (random từ kho miễn phí, không bao giờ lặp),
**thẻ chat** mô phỏng UI bình luận Threads, và **giọng đọc AI tiếng Việt** đọc từng
bình luận — card hiện đúng lúc giọng đọc chạy.

---

## Cài đặt

```powershell
winget install --id=Gyan.FFmpeg -e --source winget

python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium

copy .env.example .env      # rồi điền key
streamlit run app.py
```

### Khoá cần điền trong `.env`

| Biến | Bắt buộc | Dùng để làm gì |
|---|---|---|
| `GOOGLE_APPLICATION_CREDENTIALS` | **Có** | Đường dẫn tới service-account JSON của Google Cloud Text-to-Speech. Miễn phí 1 triệu ký tự/tháng ≈ 600 video. |
| `GEMINI_API_KEY` | Không | Gemini Flash chọn + xếp thứ tự bình luận. Free tier 1.500 request/ngày. Thiếu key thì tool tự dùng thuật toán heuristic. |
| `PEXELS_API_KEY` | Nên có | Kho video nền. Miễn phí, 200 request/giờ. |
| `PIXABAY_API_KEY` | Nên có | Kho video nền thứ hai — tool tự chuyển sang khi Pexels sắp hết quota. |

Service account cũng có thể đặt dưới dạng bảng `[gcp_service_account]` trong
`.streamlit/secrets.toml`. Private key **không bao giờ** được ghi ra file tạm.

Bỏ file nhạc nền vào `assets/music/` và tiếng chuyển card vào `assets/sfx/`
(`.mp3`, `.m4a`, `.wav`). Tool sẽ random nếu bạn chọn "(ngẫu nhiên)".

---

## Dùng

Wizard 4 bước:

1. **Nhập link** — dán link trend hoặc permalink bài viết. Trang trend cần đăng nhập
   Threads: bấm "Mở browser để đăng nhập" một lần, phiên được lưu trong
   `data/playwright-profile/`.
2. **Duyệt bình luận** — bảng cho bật/tắt từng bình luận, đổi thứ tự, sửa nội dung,
   đổi vai trò. Panel "Vì sao thứ tự này?" giải thích từng mắt xích.
3. **Tuỳ chọn** — giọng đọc vi-VN, tốc độ, tông card, nhạc nền, SFX, encoder.
4. **Render** — tiến trình theo thời gian thực, xem thử và tải về.

Mỗi lần chạy để lại toàn bộ dấu vết trong `output/<run_id>/`: `final.mp4`,
`graph.txt` (filtergraph), `timeline.json`, `manifest.json`, `cards/`, `debug/`.

---

## Cách nó chọn bình luận

Đây là phần khó nhất và là lý do video xem được chứ không phải slideshow.

**Tầng 1 — cấu trúc.** Threads đóng gói reply theo *chuỗi*: một mảng `thread_items`
độ dài N là **một reply trực tiếp + N−1 reply lồng đã bung**, và `parent_reply_id`
của phần tử thứ *i* trỏ tới phần tử *i−1*, không phải bài gốc. Phần lớn scraper
làm phẳng mảng này và đẩy tất cả về depth 1 — mất sạch thông tin ai đang trả lời ai.
`scrape/tree.py` dựng lại đúng cây, nên **reply thật = tương tác thật**, không cần suy đoán.

**Tầng 2 — chấm điểm tiếng Việt.** Lexicon có dấu, match sau khi bỏ dấu và bung
teencode, có ràng buộc biên từ (`"hả"` không khớp trong `"hàng"`). Điểm `shock`,
`explain`, `counter`, `reaction`, `punchline` chuẩn hoá theo **p95** lượt like của
chính thread đó — không theo max, nếu không một reply viral sẽ dìm mọi cái còn lại về 0.

**Tầng 3 — beam search.** Tối ưu `Σ role_fit + 0.9 · Σ cohesion` dưới ràng buộc
thời lượng. `cohesion` chấm "cái sau có đang trả lời cái trước không": reply thật
+1.00, cùng chuỗi +0.70, gọi tên nhau +0.50, trùng từ hiếm, có từ hồi chỉ; cùng tác
giả mà không phải reply bị **trừ** 0.80. Sau khi chọn hook, các con cháu của nó
trong cây reply được cộng điểm — vì một reply thật cho hook, theo định nghĩa, chính
là bình luận giải thích cho hook.

**Tầng 4 — Gemini (tuỳ chọn).** Nhận 40 ứng viên *đã lọc và đã chấm điểm*, cùng kết
quả heuristic làm baseline. Model chỉ được **chọn và xếp**, và mọi thứ nó trả về đều
bị kiểm tra lại: pk phải có thật, không trùng, đúng số lượng vai trò, và `tts_text`
phải đạt token-F1 ≥ 0.55 so với bản gốc, không chứa từ hiếm nào không có trong bản
gốc. Vi phạm → thay bằng bản chuẩn hoá tất định. Hỏng hoàn toàn → rơi về heuristic.

---

## Vài quyết định kỹ thuật đáng lưu ý

**Card không bao giờ vượt 384px.** `theme.py` giữ các hằng số cứng (864 rộng = 80%,
384 cao = 1/5, `x=108` căn giữa, `y=192` lề 10%). Renderer binary-search cỡ chữ từ
36px xuống 24px; không vừa thì phân trang, ranh giới trang trùng ranh giới chunk TTS
nên card lật đúng lúc giọng đọc tới. Có `assert` ngay sau mỗi lần render.

Phép đo phải tháo `max-height` trước khi đọc chiều cao — nếu không trình duyệt trả về
384 cho **mọi** lượng chữ, binary search luôn "thành công", và chữ bị cắt âm thầm.

**Chữ không bao giờ đi vào ffmpeg.** Chromium nướng sẵn mọi glyph vào PNG. Xoá sạch
rủi ro escape `drawtext=fontfile=` trên Windows và toàn bộ rủi ro shaping dấu tiếng Việt.

**WAV, không phải MP3.** MP3 có encoder delay, mỗi segment lệch ~26ms và sai số cộng
dồn thấy rõ từ card thứ 10. WAV cho `frames/rate` chính xác tới mẫu. Offset `adelay`
tính bằng mẫu rồi mới đổi sang ms.

**ffmpeg 8 đã gỡ `-filter_complex_script`.** Thay bằng `-/filter_complex <file>`.
`ffmpeg_locator.filter_script_args()` dò xem build nào hỗ trợ cái nào. Filtergraph
luôn ghi ra file UTF-8 **không BOM** (ffmpeg không bỏ qua BOM), và luôn gọi bằng
argv với `shell=False` — PowerShell sẽ băm nát dấu phẩy, ngoặc và chấm phẩy trong
một dòng lệnh ffmpeg.

**Card là stream loop thật, dịch bằng `tpad`.** Một PNG mở không kèm `-loop 1` chỉ là
1 frame ở PTS 0, nên `fade=t=out:st=3.1` không bao giờ nổ. `tpad` chèn frame trong
suốt thật từ t=0 nên framesync của overlay luôn có frame để dùng — tất định trên
ffmpeg 5 đến 9.

**Video nền không bao giờ lặp.** Ledger SQLite theo cơ chế reserve-then-confirm: run
crash thì trả clip lại, không đốt oan. Dedupe thêm bằng sha256 nội dung để bắt cùng
một clip mirror trên hai provider. Motion gate (`tblend=difference` + `signalstats`)
là chỗ thực thi "sôi nổi, không chill" — clip dưới ngưỡng bị đánh dấu vĩnh viễn.

---

## Kiểm thử

```powershell
.venv\Scripts\python.exe -m pytest tests/test_core.py -q      # 52 test, offline
.venv\Scripts\python.exe tests\render_cards_smoke.py          # card thật + ràng buộc 864x384
.venv\Scripts\python.exe tests\render_video_smoke.py          # toàn chuỗi ffmpeg, audio tổng hợp
.venv\Scripts\python.exe tests\ui_walkthrough.py              # lái UI qua luồng dán JSON
```

`render_cards_smoke` và `render_video_smoke` không cần mạng và không cần credential.
`ui_walkthrough` cần app đang chạy ở cổng 8577.

---

## Chưa kiểm chứng được

Hai thứ cần khoá của bạn nên chưa chạy thật lần nào:

- **Google Cloud TTS** — đường dẫn code đã đủ (LINEAR16/24kHz, `atempo` bù cho các
  giọng Chirp3-HD không nhận `speaking_rate`), nhưng chưa gọi API thật lần nào.
- **Scrape Threads thật** — cần phiên đăng nhập. Selector và cấu trúc payload dựa
  trên tài liệu công khai; luồng "dán JSON thủ công" tồn tại chính vì bước này là
  thứ dễ hỏng nhất khi Meta đổi payload.

Ngưỡng motion gate (4.5) mới hiệu chỉnh trên clip tổng hợp, chưa quét trên stock
footage thật — nên selector coi nó là *ưu tiên* chứ không phải cổng cứng: nếu mọi
ứng viên đều bị loại, nó lấy clip động nhất thay vì bỏ cuộc.
# ThreadTrending
