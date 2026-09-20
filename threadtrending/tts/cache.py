"""Content-addressed cache for synthesized speech.

The key covers everything that changes the bytes -- text, voice, rate, encoding
and sample rate -- so editing a comment in the UI invalidates exactly that one
clip and leaves the rest of the run untouched.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from ..paths import PATHS
from .constants import ENCODING_TAG, SAMPLE_RATE


def cache_key(text: str, voice_name: str, rate: float) -> str:
    payload = "|".join(
        [text.strip(), voice_name, f"{rate:.3f}", ENCODING_TAG, str(SAMPLE_RATE)]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def cache_path(text: str, voice_name: str, rate: float, out_dir: Path | None = None) -> Path:
    directory = out_dir or PATHS.cache_tts
    return directory / f"{cache_key(text, voice_name, rate)}.wav"
