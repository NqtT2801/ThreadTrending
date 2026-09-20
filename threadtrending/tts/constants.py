"""Audio format constants shared by the TTS and render stages.

Kept in their own module so ``tts.cache`` does not have to import the Google
client just to know the sample rate.
"""
from __future__ import annotations

#: PCM 16-bit. Chosen over MP3 because it has no encoder delay -- see render.probe.
ENCODING_TAG = "LINEAR16"
SAMPLE_RATE = 24_000
CHANNELS = 1
SAMPLE_WIDTH = 2
