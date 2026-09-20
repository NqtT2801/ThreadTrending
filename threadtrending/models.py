"""Data model shared by every stage. Frozen where it is genuinely immutable."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Role = Literal["hook", "context", "explain", "counter", "reaction", "punchline"]
ROLES: tuple[Role, ...] = (
    "hook", "context", "explain", "counter", "reaction", "punchline",
)


@dataclass(frozen=True, slots=True)
class Comment:
    """One node of the Threads reply forest.

    ``parent_pk`` is what makes the whole narrative layer possible: a real reply
    is a real interaction, and that beats any topical-similarity heuristic.
    """

    pk: str
    text: str
    username: str
    code: str | None = None
    profile_pic_url: str = ""
    is_verified: bool = False
    like_count: int = 0
    reply_count: int = 0
    taken_at: int = 0
    parent_pk: str | None = None
    depth: int = 1
    chain_id: str = ""
    order_in_chain: int = 0
    hidden_replies_cta: str | None = None

    @property
    def is_root(self) -> bool:
        return self.depth == 0


@dataclass(frozen=True, slots=True)
class Thread:
    """A root post plus its reply forest."""

    root: Comment
    replies: tuple[Comment, ...]
    trend_name: str = ""
    source_url: str = ""

    def by_pk(self) -> dict[str, Comment]:
        return {c.pk: c for c in (self.root, *self.replies)}


@dataclass(frozen=True, slots=True)
class Chain:
    """A root->leaf path through the reply forest: a real back-and-forth."""

    nodes: tuple[Comment, ...]
    n_speakers: int
    alternations: int
    median_latency_s: float
    heat: float

    @property
    def pks(self) -> tuple[str, ...]:
        return tuple(n.pk for n in self.nodes)


@dataclass
class Slot:
    """A curated comment with its narrative role and read-aloud text."""

    comment: Comment
    role: Role
    tts_text: str
    est_seconds: float
    reason: str = ""
    replies_to_previous: bool = False
    include: bool = True


@dataclass
class CardModel:
    """Everything the HTML template needs for one card."""

    username: str
    text: str
    avatar_data_uri: str
    is_verified: bool = False
    timestamp_label: str = ""
    like_count: int = 0
    reply_count: int = 0
    reply_to_username: str | None = None
    theme: Literal["dark", "light"] = "dark"

    @property
    def has_reply_strip(self) -> bool:
        return bool(self.reply_to_username)


@dataclass(frozen=True, slots=True)
class Page:
    """One screenful of a comment. index/of drive the page indicator."""

    text: str
    index: int
    of: int

    @property
    def is_first(self) -> bool:
        return self.index == 0


@dataclass(frozen=True, slots=True)
class RenderItem:
    """A page that has been rendered to PNG and synthesized to WAV."""

    pk: str
    page: int
    of: int
    png: Path
    audio_path: Path
    role: Role


@dataclass(frozen=True, slots=True)
class Segment:
    """One visibility window on the final timeline. Times are absolute seconds."""

    idx: int
    comment_pk: str
    page: int
    card_png: Path
    audio_path: Path
    audio_dur: float
    start: float
    audio_at: float
    end: float
    fade_in: float
    fade_out: float

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def wants_sfx(self) -> bool:
        return self.page == 0


@dataclass(frozen=True, slots=True)
class Timeline:
    segments: tuple[Segment, ...]
    total: float

    def sfx_offsets(self) -> tuple[float, ...]:
        return tuple(s.start for s in self.segments if s.wants_sfx)


@dataclass
class BackgroundClip:
    provider: str
    video_id: str
    file_url: str
    path: Path
    duration: float
    width: int
    height: int
    fps: float
    motion_score: float = 0.0
    query: str = ""
    author: str = ""
    author_url: str = ""


@dataclass
class RenderSpec:
    """The complete, resolved recipe for one render. Serialized to manifest.json."""

    run_id: str
    trend_name: str
    source_url: str
    slots: list[Slot] = field(default_factory=list)
    voice_name: str = ""
    speaking_rate: float = 1.0
    theme: Literal["dark", "light"] = "dark"
    show_reply_context: bool = True
    show_banner: bool = True
    music_path: Path | None = None
    sfx_path: Path | None = None
    music_volume: float = 0.13
    sfx_volume: float = 0.35
    encoder: Literal["libx264", "h264_qsv"] = "libx264"
    duration_budget_s: float = 50.0
