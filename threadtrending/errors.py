"""Typed failures. Each one maps to a specific, actionable message in the UI."""
from __future__ import annotations


class ThreadTrendingError(Exception):
    """Base class. Carries a Vietnamese message meant for the user."""

    hint: str = ""

    def __init__(self, message: str, *, hint: str = ""):
        super().__init__(message)
        self.hint = hint or self.hint


class LoginRequired(ThreadTrendingError):
    hint = "Bấm 'Mở browser để đăng nhập' ở bước 1, đăng nhập Threads rồi thử lại."


class ScrapeEmpty(ThreadTrendingError):
    hint = (
        "Threads có thể đã đổi cấu trúc dữ liệu. Xem output/<run>/debug/ để đối chiếu, "
        "hoặc dùng ô 'Dán JSON thủ công' ở bước 1."
    )


class CurationFailed(ThreadTrendingError):
    hint = "Thử 'Xếp lại (heuristic)' để bỏ qua Gemini."


class TtsError(ThreadTrendingError):
    hint = "Kiểm tra service-account JSON và quota Text-to-Speech của project."


class NoBackgroundAvailable(ThreadTrendingError):
    hint = (
        "Kho video nền đã cạn hoặc hết rate limit. Thêm PIXABAY_API_KEY, "
        "hoặc bỏ vài clip .mp4 vào assets/backgrounds/."
    )


class FfmpegMissing(ThreadTrendingError):
    hint = "winget install --id=Gyan.FFmpeg -e --source winget"


class FfmpegError(ThreadTrendingError):
    hint = "Xem graph.txt và 40 dòng stderr cuối trong thư mục run."


class CardOverflow(ThreadTrendingError):
    hint = "Bình luận quá dài cho một card; pagination lẽ ra phải xử lý được."
