"""Google Cloud Text-to-Speech, Vietnamese, always returning PCM WAV.

Two decisions worth knowing about:

* **LINEAR16 / 24 kHz, never MP3.** Segment timings are measured from the audio
  itself, and MP3 decoder priming would offset every segment by ~26 ms. See
  :mod:`..render.probe`.
* **Speaking rate is applied before anything is measured.** Neural2 / WaveNet /
  Standard accept ``speaking_rate`` directly. Chirp3-HD accepts neither
  ``speaking_rate`` nor SSML, so those voices are sped up afterwards with
  ffmpeg ``atempo`` and the file on disk is always the final audio.
"""
from __future__ import annotations

import functools
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from ..errors import TtsError
from ..ffmpeg_locator import FfmpegTools
from .cache import cache_path
from .constants import SAMPLE_RATE
from .tempo import apply_tempo

log = logging.getLogger(__name__)

LANGUAGE_CODE = "vi-VN"
#: Google rejects a synthesis input larger than this many bytes.
MAX_INPUT_BYTES = 4_800

#: Voice families that accept speaking_rate / pitch / SSML.
RATE_CAPABLE_PREFIXES = ("Neural2", "Wavenet", "WaveNet", "Standard", "Polyglot", "News")


@dataclass(frozen=True)
class Voice:
    name: str
    gender: str
    family: str
    natural_sample_rate: int = 24_000

    @property
    def supports_speaking_rate(self) -> bool:
        return self.family in ("Neural2", "WaveNet", "Standard", "Polyglot", "News")

    @property
    def label(self) -> str:
        short = self.name.rsplit("-", 1)[-1]
        return f"{self.family} · {short} ({self.gender.lower()})"


def _family_of(voice_name: str) -> str:
    tail = voice_name[len(LANGUAGE_CODE) + 1 :] if voice_name.startswith(LANGUAGE_CODE) else voice_name
    for marker, family in (
        ("Chirp3-HD", "Chirp3-HD"), ("Chirp-HD", "Chirp-HD"), ("Chirp", "Chirp"),
        ("Neural2", "Neural2"), ("Wavenet", "WaveNet"), ("Studio", "Studio"),
        ("Polyglot", "Polyglot"), ("News", "News"), ("Standard", "Standard"),
    ):
        if tail.startswith(marker):
            return family
    return "Khác"


#: Family ordering for the picker: best-sounding first.
FAMILY_ORDER = {
    "Chirp3-HD": 0, "Chirp-HD": 1, "Chirp": 2, "Studio": 3,
    "Neural2": 4, "WaveNet": 5, "Polyglot": 6, "News": 7, "Standard": 8, "Khác": 9,
}


def build_client(credentials: dict[str, Any]):
    """Client from an in-memory service account. No temp file is ever written."""
    try:
        from google.cloud import texttospeech
        from google.oauth2 import service_account
    except ImportError as exc:  # pragma: no cover
        raise TtsError("Thiếu google-cloud-texttospeech.", hint="pip install -r requirements.txt") from exc

    if not credentials or "private_key" not in credentials:
        raise TtsError("Chưa cấu hình service account cho Google Cloud TTS.")
    creds = service_account.Credentials.from_service_account_info(credentials)
    return texttospeech.TextToSpeechClient(credentials=creds)


@functools.lru_cache(maxsize=4)
def _cached_voice_list(client_id: int, client_ref: Any) -> tuple[Voice, ...]:
    from google.cloud import texttospeech

    response = client_ref.list_voices(
        request=texttospeech.ListVoicesRequest(language_code=LANGUAGE_CODE)
    )
    voices = [
        Voice(
            name=v.name,
            gender=texttospeech.SsmlVoiceGender(v.ssml_gender).name.title(),
            family=_family_of(v.name),
            natural_sample_rate=v.natural_sample_rate_hertz or SAMPLE_RATE,
        )
        for v in response.voices
    ]
    voices.sort(key=lambda v: (FAMILY_ORDER.get(v.family, 99), v.name))
    return tuple(voices)


def list_voices(client) -> list[Voice]:
    """Live vi-VN catalogue. Never hardcoded -- Google adds voices regularly."""
    try:
        return list(_cached_voice_list(id(client), client))
    except Exception as exc:
        raise TtsError(f"Không lấy được danh sách giọng: {exc}") from exc


def default_voice(voices: Sequence[Voice]) -> Voice | None:
    """Prefer a rate-controllable neural voice, then the best-sounding family."""
    for voice in voices:
        if voice.family == "Neural2":
            return voice
    return voices[0] if voices else None


def synthesize(
    client,
    text: str,
    voice: Voice,
    *,
    rate: float = 1.0,
    tools: FfmpegTools | None = None,
    out_dir: Path | None = None,
) -> Path:
    """Synthesize one utterance to a PCM WAV file, applying ``rate`` first.

    Content-addressed: identical (text, voice, rate) never hits the API twice.
    """
    from google.cloud import texttospeech

    text = (text or "").strip()
    if not text:
        raise TtsError("Không thể tổng hợp chuỗi rỗng.")
    if len(text.encode("utf-8")) > MAX_INPUT_BYTES:
        raise TtsError(f"Đoạn văn bản dài quá {MAX_INPUT_BYTES} byte cho một request TTS.")

    target = cache_path(text, voice.name, rate, out_dir=out_dir)
    if target.exists() and target.stat().st_size > 44:
        return target

    api_rate = rate if voice.supports_speaking_rate else 1.0
    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.LINEAR16,
        sample_rate_hertz=SAMPLE_RATE,
        **({"speaking_rate": api_rate} if voice.supports_speaking_rate else {}),
    )

    try:
        response = client.synthesize_speech(
            request=texttospeech.SynthesizeSpeechRequest(
                input=texttospeech.SynthesisInput(text=text),
                voice=texttospeech.VoiceSelectionParams(
                    language_code=LANGUAGE_CODE, name=voice.name
                ),
                audio_config=audio_config,
            )
        )
    except Exception as exc:
        raise TtsError(f"Google TTS lỗi: {exc}") from exc

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(response.audio_content)

    # Chirp3-HD ignores speaking_rate, so the speed-up happens in ffmpeg and the
    # cached file still reflects the rate that is baked into its cache key.
    if not voice.supports_speaking_rate and abs(rate - 1.0) > 0.01:
        if tools is None:
            log.warning("Giọng %s không chỉnh được tốc độ và thiếu ffmpeg để bù", voice.name)
        else:
            apply_tempo(tools, target, rate)

    return target
