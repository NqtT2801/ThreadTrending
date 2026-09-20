"""Filesystem layout. Every directory the app writes to is created here, once.

The run directory deliberately lives under the project root rather than under the
user profile: ffmpeg input paths are passed through argv and a home directory
containing Vietnamese diacritics has bitten this pipeline before.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Paths:
    root: Path = ROOT
    assets: Path = ROOT / "assets"
    fonts: Path = ROOT / "threadtrending" / "cards" / "assets" / "fonts"
    music: Path = ROOT / "assets" / "music"
    sfx: Path = ROOT / "assets" / "sfx"
    emergency_backgrounds: Path = ROOT / "assets" / "backgrounds"

    data: Path = ROOT / "data"
    profile: Path = ROOT / "data" / "playwright-profile"
    ledger: Path = ROOT / "data" / "ledger.sqlite3"

    cache: Path = ROOT / "cache"
    cache_threads: Path = ROOT / "cache" / "threads"
    cache_tts: Path = ROOT / "cache" / "tts"
    cache_cards: Path = ROOT / "cache" / "cards"
    cache_avatars: Path = ROOT / "cache" / "avatars"
    cache_backgrounds: Path = ROOT / "cache" / "backgrounds"

    output: Path = ROOT / "output"
    logs: Path = ROOT / "data" / "logs"

    def ensure(self) -> "Paths":
        for p in (
            self.assets, self.music, self.sfx, self.emergency_backgrounds,
            self.data, self.profile, self.cache, self.cache_threads,
            self.cache_tts, self.cache_cards, self.cache_avatars,
            self.cache_backgrounds, self.output, self.logs,
        ):
            p.mkdir(parents=True, exist_ok=True)
        return self

    def run_dir(self, run_id: str) -> Path:
        d = self.output / run_id
        for sub in ("cards", "seg", "debug"):
            (d / sub).mkdir(parents=True, exist_ok=True)
        return d


PATHS = Paths().ensure()


def new_run_id() -> str:
    return "r_" + datetime.now().strftime("%Y%m%d_%H%M%S")
