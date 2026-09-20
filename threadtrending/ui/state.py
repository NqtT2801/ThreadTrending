"""Session state and cached resources.

The browser manager lives in ``st.cache_resource`` and is shared across reruns.
That matters for more than speed: Playwright's sync API refuses to run on a
thread that owns an asyncio event loop, and Streamlit's ScriptRunner thread is
recreated on every rerun. :class:`~..scrape.browser.BrowserManager` pins all
Playwright work to one long-lived worker, and caching the manager is what keeps
that worker alive between reruns.
"""
from __future__ import annotations

import atexit
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

import streamlit as st

from .. import ffmpeg_locator as fl
from ..background.ledger import Ledger
from ..config import Settings, load as load_settings
from ..logging_setup import set_run_id, setup as setup_logging
from ..models import RenderSpec
from ..paths import PATHS, new_run_id
from ..scrape.browser import BrowserManager


class Step(IntEnum):
    INPUT = 1
    REVIEW = 2
    OPTIONS = 3
    RENDER = 4


STEP_LABELS = {
    Step.INPUT: "1 · Nhập link",
    Step.REVIEW: "2 · Duyệt bình luận",
    Step.OPTIONS: "3 · Tuỳ chọn",
    Step.RENDER: "4 · Render",
}


@dataclass
class Preflight:
    tools: Any
    warnings: list[str]
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.tools is not None and not self.error


@st.cache_resource(show_spinner=False)
def browser_manager() -> BrowserManager:
    manager = BrowserManager(PATHS.profile)
    atexit.register(manager.shutdown)
    return manager


@st.cache_resource(show_spinner=False)
def ledger() -> Ledger:
    store = Ledger()
    store.prune_calls()
    return store


@st.cache_resource(show_spinner=False)
def logging_ready() -> bool:
    setup_logging()
    return True


@st.cache_data(ttl=300, show_spinner=False)
def _preflight_cached(ffmpeg_hint: str, ffprobe_hint: str) -> tuple[Any, list[str], str]:
    from pathlib import Path

    try:
        tools = fl.resolve(
            Path(ffmpeg_hint) if ffmpeg_hint else None,
            Path(ffprobe_hint) if ffprobe_hint else None,
        )
        return tools, fl.preflight(tools), ""
    except Exception as exc:
        return None, [], str(exc)


def preflight(settings: Settings) -> Preflight:
    tools, warnings, error = _preflight_cached(
        str(settings.ffmpeg_path or ""), str(settings.ffprobe_path or "")
    )
    return Preflight(tools=tools, warnings=list(warnings), error=error)


def settings() -> Settings:
    if "settings" not in st.session_state:
        st.session_state.settings = load_settings()
    return st.session_state.settings


def reload_settings() -> Settings:
    st.session_state.settings = load_settings()
    return st.session_state.settings


def init() -> None:
    logging_ready()
    st.session_state.setdefault("step", Step.INPUT)
    st.session_state.setdefault("run_id", new_run_id())
    st.session_state.setdefault("url", "")
    st.session_state.setdefault("pasted_json", "")
    st.session_state.setdefault("prep", None)
    st.session_state.setdefault("spec", None)
    st.session_state.setdefault("result", None)
    st.session_state.setdefault("login_state", None)
    st.session_state.setdefault("voices", None)
    st.session_state.setdefault("last_error", "")
    set_run_id(st.session_state.run_id)


def goto(step: Step) -> None:
    st.session_state.step = step
    st.rerun()


def new_run() -> str:
    run_id = new_run_id()
    st.session_state.run_id = run_id
    set_run_id(run_id)
    return run_id


def current_spec() -> RenderSpec | None:
    return st.session_state.get("spec")


def reset_run() -> None:
    for key in ("prep", "spec", "result", "last_error"):
        st.session_state[key] = None if key != "last_error" else ""
    new_run()
